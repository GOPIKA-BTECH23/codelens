# ADR 0002: Session cookies, same-site proxy and CSRF protection (M2)

Status: accepted, 2026-10-16

## Context

In production the web app is on Vercel (`https://<app>.vercel.app`) and the API on Render (`https://<api>.onrender.com`). These are different **sites** (registrable domains), not just different origins. Our sessions live in **httpOnly cookies** (CLAUDE.md section 9), so where the browser thinks the API lives decides whether those cookies are sent at all.

There are two options (the M2 brief, and CLAUDE.md decision 1):

- **(a) Same site through a proxy.** The browser calls `https://<app>.vercel.app/api/...`, and a Vercel rewrite forwards it to Render with `/api` stripped. Locally the Vite dev server does the same. To the browser there is only one origin.
- **(b) Cross-site cookies.** The browser calls Render directly with `fetch(..., { credentials: "include" })`; cookies are `SameSite=None; Secure`, and CORS allows the web origin with credentials.

## Decision

**Option (a).** All browser traffic goes through `WEB_ORIGIN/api/*`. Cookies are host-only (no `Domain` attribute), `HttpOnly`, `SameSite=Lax`, and `Secure` everywhere except `ENVIRONMENT=development|test`.

| Cookie | Holds | Path | Lifetime |
| --- | --- | --- | --- |
| `cl_access` | HS256 JWT: user id only | `/` | `JWT_ACCESS_TTL_MINUTES` (15) |
| `cl_refresh` | 32 random bytes (only the SHA-256 is stored) | `/api/auth` | until the session's absolute expiry (`JWT_REFRESH_TTL_DAYS`, 7) |
| `cl_oauth` | signed JWT: OAuth `state` + PKCE verifier | `/api/auth/github` | 10 minutes |

The GitHub OAuth callback is therefore `WEB_ORIGIN/api/auth/github/callback` (locally `http://localhost:5173/api/auth/github/callback`), so the state cookie and the session cookies belong to the web origin.

### Why not (b)

1. **Third-party cookies are being blocked.** A `vercel.app` page sending cookies to `onrender.com` is a third-party cookie. Safari (ITP) and Firefox (Total Cookie Protection) partition or block them already; logins would break for part of our users, depending on browser settings we cannot control.
2. **`SameSite=None` throws away CSRF layer 1.** With (a) the browser itself refuses to attach our cookies to cross-site POSTs. With (b) it attaches them everywhere, and CSRF protection rests on the custom header alone.
3. **CORS with credentials is easy to get subtly wrong** (reflecting `Origin`, `*` with credentials, preflight caching). With (a) CORS is not involved in normal operation at all.

### Costs of (a), accepted

- Every API request makes one extra hop through Vercel's edge (a few ms to tens of ms).
- **Vercel caches proxied responses that look cacheable.** For projects created after 2026-04-06, external rewrites honour upstream `Cache-Control` by default (Vercel docs, "Rewrites", checked 2026-10-15). A cached `/me` response would serve one user's data to another. Defence: the API sends `Cache-Control: no-store` on every response (`app/core/headers.py`). M7 also sets `x-vercel-enable-rewrite-caching: 0` on `/api/*` as a second layer.
- **Client IP.** Behind the proxy, the TCP peer is Vercel. Rate limiting by IP needs uvicorn's `--forwarded-allow-ips` set to trusted proxies only (M7). Vercel forwards `x-real-ip` and documents an origin-secret header for "only accept traffic that came through Vercel"; M7 decides whether to use it.

## SSE streaming for chat (M9): the open risk

Repo chat streams answers with Server-Sent Events. Option (a) puts Vercel's proxy in the stream's path. On 2026-10-15 Vercel's rewrite documentation says nothing about streaming or timeouts for **external** rewrites; community reports describe buffered or truncated streams for Vercel *functions*, which are a different path. So this is **unverified**, not known to be broken.

Plan (CLAUDE.md decision 1, unchanged):

1. **M7** deploys a temporary `/debug/sse` endpoint behind a flag and checks that events arrive incrementally through the rewrite, then removes it and records the result here.
2. If the rewrite buffers, **serve the built React app from FastAPI** (one origin on Render, no proxy at all). Cookies, CSRF and the OAuth callback stay the same; only the hosting of the static files moves.
3. A paid custom domain (web and API on subdomains of one site, still same-site) only if both fail.

The cookie and CSRF design here works unchanged under all three, which is the main reason it is safe to decide now.

## CSRF protection

CSRF: another site makes the victim's browser send a request to us, with the victim's cookies attached. Defence in depth:

1. **`SameSite=Lax`** on every cookie: browsers do not attach them to cross-site POST/PUT/PATCH/DELETE (or to cross-site subresource requests). Top-level GET navigations still carry them, which the OAuth callback needs.
2. **Required custom header** (`app/core/csrf.py`), deny by default: every non-GET/HEAD/OPTIONS request must carry `X-Requested-With: codelens`, or it gets `403 csrf_failed`. HTML forms cannot set headers; a cross-origin `fetch` that sets one triggers a CORS preflight, which our CORS policy (locked to `WEB_ORIGIN`) refuses.
3. **Origin check**: if the browser sent `Origin`, it must equal `WEB_ORIGIN`.
4. **GET never changes state**, so layers 1-3 only need to guard unsafe methods.

The OAuth login has its own CSRF protection: the `state` parameter, checked in constant time against the signed `cl_oauth` cookie. Without it, an attacker could make a victim's browser complete a login the attacker started ("login CSRF"), silently signing the victim into the attacker's account. PKCE additionally binds the authorization code to this browser's attempt.

Exemptions: none in M2. M3 exempts `POST /webhooks/github`, which carries no cookies and is authenticated by its HMAC signature instead.

## Consequences

- The web app never sees a token. "Am I logged in?" is answered only by `GET /me`.
- Any new state-changing endpoint is CSRF-protected automatically; forgetting is not possible.
- Every API response is `no-store`. If a future endpoint is genuinely public and cacheable, it must opt in explicitly.
- M7 must: configure the Vercel rewrite and the caching header, set uvicorn's trusted proxies, register the production callback URL on the production GitHub App, and run the SSE check above.
