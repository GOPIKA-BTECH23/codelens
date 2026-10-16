"""CSRF protection for state-changing requests (pure ASGI middleware). See ADR 0002.

The attack: a page on evil.example submits a form or fetch to our API, and the browser
attaches the victim's cookies. Our layers:

1. SameSite=Lax cookies: browsers do not send them on cross-site POST/PUT/PATCH/DELETE.
2. This middleware (deny by default): every unsafe request must carry the header
   `X-Requested-With: codelens`. HTML forms cannot set custom headers, and a cross-origin
   fetch that sets one triggers a CORS preflight, which our CORS policy only allows for
   WEB_ORIGIN. So only our own front end can produce the header.
3. If the browser sent an Origin header, it must be WEB_ORIGIN exactly.
"""

import json
import logging

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "codelens"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

logger = logging.getLogger("app.security")


class CsrfMiddleware:
    """Reject unsafe requests that lack the custom header or come from a foreign origin."""

    def __init__(
        self, app: ASGIApp, *, web_origin: str, exempt_paths: frozenset[str] = frozenset()
    ) -> None:
        self.app = app
        self.web_origin = web_origin
        # Endpoints authenticated by something other than cookies (M3: the GitHub webhook,
        # which is verified by HMAC). Empty in M2.
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in SAFE_METHODS
            or scope["path"] in self.exempt_paths
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE:
            reason = "missing_header"
        elif origin is not None and origin != self.web_origin:
            reason = "foreign_origin"
        else:
            await self.app(scope, receive, send)
            return

        logger.warning(
            "csrf check failed",
            extra={"reason": reason, "method": scope["method"], "path": scope["path"]},
        )
        # Middleware sits outside FastAPI's exception handlers, so build the standard error
        # shape by hand. The request-ID middleware (outermost) still adds X-Request-ID.
        body = json.dumps(
            {"error": {"code": "csrf_failed", "message": "Cross-site request rejected"}},
            separators=(",", ":"),  # same compact JSON as every other error response
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
