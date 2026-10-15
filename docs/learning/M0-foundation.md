# M0: Foundation

## What was built

A monorepo that boots end to end with "hello world" on every layer, and CI from day one.

- **`apps/api`**: a FastAPI app with `GET /health`, a validated `Settings` class, JSON logs with request IDs, and one global place that turns every error into `{"error": {"code", "message"}}`.
- **`apps/web`**: a Vite + React 19 + strict TypeScript + Tailwind page that calls `/health` through a typed client and shows the result (TanStack Query for server state, React Router for routing).
- **`docker-compose.yml`**: Postgres (with the pgvector extension), the API and the web dev server, started with one command.
- **`.github/workflows/ci.yml`**: lint + tests for the API (with a Postgres service container) and lint + typecheck + tests + build for the web app.
- **`.env.example`**, `.gitignore`, `.gitattributes` (LF line endings), README skeleton.

## How the pieces talk to each other

```
browser ──GET /api/health──► Vite dev server (:5173)
                                │  proxy: strip "/api", forward
                                ▼
                         FastAPI (:8000)  ──(M1+)──► Postgres (:5432)
```

1. The React page asks TanStack Query for `["health"]`. The query function calls `api.health()`, which does `fetch("/api/health")`.
2. The browser only ever talks to the Vite server (same origin). Vite's proxy removes the `/api` prefix and forwards to `http://api:8000/health` (inside compose, `api` is the service name; Docker's internal DNS resolves it).
3. The request-ID middleware tags the request, FastAPI runs the `/health` handler, and the response comes back with an `X-Request-ID` header. One JSON log line records the method, path, status and duration.
4. The page renders status, version and database (`not_configured` until M1).

Startup order is enforced by `depends_on` with health conditions: Postgres is healthy → API starts → API is healthy → web starts.

## What a Docker healthcheck is

A command Docker runs inside the container on a schedule (`pg_isready` for Postgres; a tiny Python `urlopen` of `/health` for the API). The container's state becomes `healthy` or `unhealthy`. A running process is not the same as a ready service: Postgres takes a few seconds to accept connections after its process starts. `depends_on: condition: service_healthy` makes compose wait for readiness, not just for "started". Render and other hosts use the same idea with a health check path.

## Why CI runs from day one

- The cheapest time to fix a broken build is the first commit; every later milestone then starts from a known-green baseline.
- It proves the project builds from a clean checkout, not only on your machine (for example, missing lockfile entries or Windows-only assumptions show up immediately).
- The pipeline is built up gradually (a Postgres service container now, E2E in M12) instead of as a big-bang setup at the end.
- Branch protection (later) needs checks that already exist.

## Why each decision was made

| Decision | Why |
| --- | --- |
| Only `DATABASE_URL` and `WEB_ORIGIN` are required | A clean clone must boot before the GitHub App or LLM keys exist. Each milestone promotes its own variables to required. |
| `create_app()` factory, run with `uvicorn --factory` | Settings load at startup (fail fast), and tests can build an app with explicit settings. |
| `DATABASE_URL` is a `SecretStr` | The password never shows up in a log line or a `repr`. |
| Pure-ASGI request-ID middleware | Works for streaming responses later (chat SSE). Client-supplied IDs are accepted only if they match a strict pattern, so they can't forge log lines. |
| 500 responses return a generic message | Internals (stack traces, SQL, file paths) go to the logs only. The client gets the request ID to quote. |
| Vite proxy under `/api` | Matches the production plan (single origin, decisions log #1), so cookies and CORS behave the same in dev and prod. |
| Hand-written API types in one file | One source of truth for now; OpenAPI generation is revisited in M6. |
| Actions pinned to commit SHAs | A moved or hijacked tag can't change what runs in our pipeline. |
| Images pinned to exact tags | Reproducible builds: pgvector `0.8.7-pg17-bookworm`, Python `3.12.15`, Node `22.23.3`, uv `0.13.1`. |

### Version choices that were not "just take latest"

- **TypeScript 6.0.3, not 7.x:** `typescript-eslint` currently supports TypeScript below 6.1, so 7 would break linting.
- **React Router 7.18.4, not 8.x:** v8 needs Node 22.22+, and the development machine runs 22.14. Upgrade after updating Node.
- **jsdom 29.1.1:** the newest jsdom needs Node 22.22+ as well.
- **`httpx2` for the API tests:** Starlette's test client now prefers it and warns when given `httpx`. (Heads-up for M3: the mocking library `respx` targets `httpx`; we will verify the combination then.)

## Likely interview questions

1. **Why does `/health` exist, and why does it report the database separately from "status"?** Orchestrators and uptime pingers need a cheap "is the process alive" signal; reporting the dependency separately lets you tell "API up, DB down" apart from "API down".
2. **Why a Vite proxy instead of enabling CORS?** The browser sees one origin, so there are no cross-site cookies or preflight requests, and it mirrors production. CORS is still locked down properly in M2.
3. **What does `depends_on` with `service_healthy` give you over plain `depends_on`?** Plain `depends_on` waits for the container to start; the health condition waits until the service is actually ready to accept work.
4. **Why return a generic 500 message and put details only in logs?** Error details leak internals to attackers; a request ID lets you find the full traceback server-side.
5. **Why fail fast on missing configuration?** A misconfigured app should refuse to start with a clear error rather than fail later at 2 a.m. on the first request that needs the missing value.

## Things to try breaking

- Delete `DATABASE_URL` from the API's environment and start it: read the error.
- Send `X-Request-ID: bad id` and a valid one with `curl -i`: which one is echoed?
- Add a route that raises an exception: what does the client see, and what does the log show?
- Stop the `api` container while the page is open and reload: which UI state appears?
- Change a line in `HealthPage.tsx` and watch hot reload (this works because of polling over the bind mount).
- Make `ruff format --check` fail, push a branch, and watch CI turn red.
