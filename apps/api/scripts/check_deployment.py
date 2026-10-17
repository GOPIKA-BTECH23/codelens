"""Check a LIVE deployment through the Vercel proxy (M7, ADR 0002). Standard library only.

    uv run python -m scripts.check_deployment https://codelens-gopika.vercel.app

Checks, all through the web origin (the way a browser reaches the API):
  1. /api/health answers JSON from the API and is not cached by Vercel.
  2. The OAuth start sets a host-only, HttpOnly, Secure, SameSite=Lax cookie (cookies survive
     the proxy; the login redirect is not followed).
  3. /api/debug/sse (needs DEBUG_SSE_ENABLED=true on Render) delivers events one by one, not
     all at the end: streaming survives the Vercel rewrite.
Exit code 0 only if everything passed. Prints evidence so it can be pasted into the ADR.
"""

import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


def check_health(base: str) -> bool:
    req = urllib.request.Request(f"{base}/api/health", headers={"User-Agent": "codelens-check"})
    with urllib.request.urlopen(req, timeout=90) as resp:  # a cold start can take ~1 minute
        body = json.loads(resp.read())
        cache = resp.headers.get("cache-control", "")
        vercel_cache = resp.headers.get("x-vercel-cache", "")
    print(f"[health] {body} cache-control={cache!r} x-vercel-cache={vercel_cache!r}")
    ok = body.get("status") == "ok" and body.get("database") == "ok"
    ok = ok and "no-store" in cache and vercel_cache not in ("HIT", "STALE")
    print(f"[health] {'PASS' if ok else 'FAIL'}")
    return ok


def check_cookie(base: str) -> bool:
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"{base}/api/auth/github/login")
    try:
        resp = opener.open(req, timeout=60)
    except urllib.error.HTTPError as err:  # the 3xx we refuse to follow arrives as an error
        resp = err
    cookies = resp.headers.get_all("set-cookie") or []
    location = resp.headers.get("location", "")
    print(f"[cookie] status={resp.status} location={location.split('?')[0]!r}")
    for c in cookies:
        print(f"[cookie] Set-Cookie: {c.split('=', 1)[0]}=<redacted>;{c.split(';', 1)[1]}")
    oauth = next((c for c in cookies if c.startswith("cl_oauth=")), "")
    low = oauth.lower()
    ok = (
        resp.status in (302, 307)
        and location.startswith("https://github.com/login/oauth/authorize")
        and "httponly" in low
        and "secure" in low
        and "samesite=lax" in low
        and "domain=" not in low
        and "path=/api/auth/github" in low
    )
    print(f"[cookie] {'PASS' if ok else 'FAIL'}")
    return ok


def check_sse(base: str, count: int = 8) -> bool:
    url = urllib.parse.urlparse(f"{base}/api/debug/sse?count={count}&interval=1")
    conn = http.client.HTTPSConnection(url.netloc, timeout=90)
    conn.request("GET", f"{url.path}?{url.query}", headers={"Accept": "text/event-stream"})
    resp = conn.getresponse()
    if resp.status != 200:
        print(f"[sse] status {resp.status}: is DEBUG_SSE_ENABLED=true on Render?")
        return False
    start = time.monotonic()
    arrivals: list[float] = []
    while True:
        line = resp.readline()
        if not line:
            break
        if line.startswith(b"data: ") and not line.startswith(b"data: end"):
            arrivals.append(time.monotonic() - start)
            print(f"[sse] event {len(arrivals)} arrived at +{arrivals[-1]:.1f}s")
    spread = arrivals[-1] - arrivals[0] if len(arrivals) > 1 else 0.0
    # The server sends one tick per second. Buffered = everything arrives together (spread ~0).
    ok = len(arrivals) == count and spread >= (count - 1) * 0.7
    print(
        f"[sse] first-to-last spread {spread:.1f}s for {count} events -> {'PASS' if ok else 'FAIL'}"
    )
    return ok


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")
    results = [check_health(base), check_cookie(base), check_sse(base)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
