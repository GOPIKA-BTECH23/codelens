"""Security headers on every API response (pure ASGI, so streaming responses get them too).

The API only serves JSON (and redirects), so the policy is strict: nothing may be loaded,
framed or sniffed. The interactive docs pages need scripts and styles from a CDN, so they
get no CSP. A route that sets one of these headers itself keeps its own value.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})

_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # The OAuth callback URL carries `code` and `state`; never leak URLs via Referer.
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # Responses are per-user. no-store keeps them out of browser caches AND out of the
    # Vercel CDN, which caches proxied responses that look cacheable (see ADR 0002).
    "Cache-Control": "no-store",
}
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        # HSTS only when served over HTTPS (production); on http://localhost it would be
        # ignored at best and pin the browser at worst.
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_docs = scope["path"] in _DOCS_PATHS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if not is_docs:
                    headers.setdefault("Content-Security-Policy", _API_CSP)
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", _HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
