"""Optional Sentry error reporting with PII scrubbing (CLAUDE.md section 9).

Off unless SENTRY_DSN is set. What we never send: cookies, auth headers, request bodies,
query strings (the OAuth callback carries a one-time code), user identity, local variables
of stack frames (they can hold source code or tokens) and log breadcrumbs' message bodies
are not needed to debug, so we keep only the event type and the stack.
"""

from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import Settings

_SCRUBBED = "[scrubbed]"
_SENSITIVE_HEADERS = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "x-hub-signature-256",
        "x-hub-signature",
        "x-requested-with",
        "x-forwarded-for",
        "x-real-ip",
    }
)


def scrub_event(event: dict[str, Any], hint: object | None = None) -> dict[str, Any] | None:
    """`before_send` hook: strip request data and stack-frame variables from an event."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)  # request body
        request.pop("query_string", None)
        request.pop("env", None)  # includes REMOTE_ADDR
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in list(headers):
                if name.lower() in _SENSITIVE_HEADERS:
                    headers[name] = _SCRUBBED
    event.pop("user", None)
    for exception in (event.get("exception") or {}).get("values", []):
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
    event.pop("breadcrumbs", None)
    return event


def init_sentry(settings: Settings) -> bool:
    """Start Sentry if a DSN is configured. Returns whether it is on."""
    if settings.sentry_dsn is None:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        release=settings.release,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,  # errors only; no performance tracing on the free quota
        before_send=scrub_event,  # type: ignore[arg-type]  # SDK types events as TypedDict
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    return True
