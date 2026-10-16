"""POST /webhooks/github: the only door GitHub knocks on.

Order matters, and every step is cheap:
1. read the RAW body (capped), because the signature covers these exact bytes;
2. verify X-Hub-Signature-256 before trusting anything else in the request (401);
3. check the delivery/event headers and parse JSON (400);
4. hand off to the service, which records the delivery and enqueues work in one
   transaction; the commit happens before the response is sent (SessionDep).
202 = accepted (including deliberately ignored events), 200 = duplicate delivery.
"""

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import BadRequestError, InvalidSignatureError, PayloadTooLargeError
from app.core.webhook_signature import SIGNATURE_HEADER, verify_signature
from app.deps import JobQueueDep, SessionDep, SettingsDep
from app.services import webhooks

router = APIRouter(tags=["webhooks"])

WEBHOOK_PATH = "/webhooks/github"
# GitHub caps payloads at 25 MB and does not deliver larger ones. The cap applies BEFORE the
# signature check, so an anonymous client cannot make us buffer an unbounded body.
MAX_BODY_BYTES = 25 * 1024 * 1024
_MAX_DELIVERY_ID_LENGTH = 64  # webhook_deliveries.delivery_id
_MAX_EVENT_LENGTH = 50  # webhook_deliveries.event


@router.post(WEBHOOK_PATH, status_code=202)
async def github_webhook(
    request: Request, db: SessionDep, settings: SettingsDep, queue: JobQueueDep
) -> JSONResponse:
    body = await _read_body(request, MAX_BODY_BYTES)
    secret = settings.github_webhook_secret.get_secret_value()
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        raise InvalidSignatureError()

    delivery_id = _required_header(request, "X-GitHub-Delivery", _MAX_DELIVERY_ID_LENGTH)
    event = _required_header(request, "X-GitHub-Event", _MAX_EVENT_LENGTH)
    payload = _parse_json_object(body)

    result = await webhooks.process_delivery(
        db,
        queue,
        webhooks.WebhookPolicy(ignored_pr_authors=settings.webhook_ignored_pr_authors),
        delivery_id=delivery_id,
        event=event,
        payload=payload,
    )
    content: dict[str, str] = {"status": result.status}
    if result.reason:
        content["reason"] = result.reason
    return JSONResponse(status_code=200 if result.status == "duplicate" else 202, content=content)


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError()
    chunks: list[bytes] = []
    size = 0
    # Count while streaming too: Content-Length can be absent (chunked) or lie.
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError()
        chunks.append(chunk)
    return b"".join(chunks)


def _required_header(request: Request, name: str, max_length: int) -> str:
    value = request.headers.get(name, "").strip()
    if not value or len(value) > max_length:
        raise BadRequestError(f"Missing or invalid {name} header")
    return value


def _parse_json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadRequestError("Body must be JSON (set the webhook content type to JSON)") from None
    if not isinstance(payload, dict):
        raise BadRequestError("Body must be a JSON object")
    return payload
