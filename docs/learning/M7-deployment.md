# M7: Deploying the MVP

## What was built

- `apps/api/Dockerfile`: production image. Stage 1 (`builder`) installs the locked dependencies with uv; stage 2 copies only the finished virtualenv and the app, runs as a non-root user (uid 10001) and has a `HEALTHCHECK`. 385 MB, no pytest/ruff/mypy inside.
- `apps/api/app/db/migrate.py`: runs `alembic upgrade head` under a Postgres **advisory lock**. The container's start command is `python -m app.db.migrate && exec uvicorn ...`.
- `infra/render.yaml`: the Render Blueprint (one free Docker web service, health check `/health`, auto-deploy **off**, worker inside the API via `RUN_WORKER_IN_API=true`).
- `apps/web/vercel.json`: the `/api/*` rewrite to Render, no rewrite caching, SPA fallback, security headers, auto-deploy **off** for `main`.
- `.github/workflows/deploy.yml`: after CI succeeds on `main`, call the Render and Vercel deploy hooks, then smoke test.
- Sentry (optional, off without a DSN) in the API, worker and web app, with scrubbing that is unit tested.
- `/health` now returns `commit`; `scripts/check_deployment.py` checks cookies and caching through Vercel (its SSE check was run once against a temporary `/debug/sse` endpoint, which has since been removed).
- `docs/deployment.md` (env var table, rollback, secret rotation, App settings), ADR 0002 section.

## What each hosting piece does

| Piece | Job | Free-tier catch |
| --- | --- | --- |
| **Vercel** | serves the static React build from its CDN and proxies `/api/*` to Render, so the browser sees one origin (ADR 0002) | none that matter here |
| **Render** | runs our Docker image: FastAPI **and** the worker loop in one process | sleeps after 15 min idle, ~1 min cold start, 512 MB RAM, 750 h/month |
| **Supabase** | managed Postgres + pgvector: data **and** the job queue | 500 MB; use the *session pooler* URL |
| **GitHub Actions** | CI, then the gated deploy and the smoke test | |
| **GitHub App "CodeLens"** | the production identity: login (OAuth), webhooks, posting reviews | "Only on this account" until cost/privacy controls exist |

## How env vars and secrets flow

Secrets never enter the repo. They live in the provider dashboards: `DATABASE_URL`, the GitHub App's client secret, webhook secret and private key, and the Groq key in **Render**; the deploy-hook URLs in **GitHub Actions secrets**. `render.yaml` only lists the names (`sync: false`), so Render asks for the values once. At start-up, `Settings` validates them and the app refuses to boot with a placeholder or a malformed key, so a bad paste fails the deploy (and Render keeps the old version) instead of failing at the first review. The web build has no secrets at all.

## What happens when you merge to `main`

1. CI (tests, lint, types, build). 2. Only if green, Deploy starts. 3. It POSTs the Render hook with the tested commit; Render builds the image. 4. New container: migrate under the lock, start uvicorn. 5. Render checks `/health`, switches traffic, SIGTERMs the old instance (its worker drains jobs for 20 s and releases the rest). 6. Deploy POSTs the Vercel hook. 7. Smoke test waits until `/health` reports the **new commit** and `database: ok`, then checks the web app and `/api/health` through the rewrite.

## Why each decision

- **Advisory lock, because Alembic has none.** I checked the Alembic 1.20.0 source: no lock call. Two instances starting together could both apply the same revision. A *session* advisory lock on a separate connection makes the second wait and then find nothing to do. It must be a separate connection: taking it on Alembic's own connection starts a transaction Alembic does not expect, and the DDL never commits. It needs a session-mode connection, hence the session pooler.
- **Migrate in the start command, not a pre-deploy command.** Works on every plan. A failed migration means the container never becomes healthy and the old instance keeps serving.
- **Both auto-deploys off, deploy through hooks.** Otherwise Render/Vercel would deploy on push regardless of CI. The hook is the gate.
- **`/health` returns the commit.** Without it a smoke test could pass against the old instance.
- **Worker in the API.** There is no free Render worker (ADR 0004 lists the trade-offs: shared 512 MB and event loop, sleeps with the API).
- **Webhook straight to Render.** A proxy might re-serialise the body and break the HMAC.
- **Same-origin proxy, not cross-site cookies.** Third-party cookies break in Safari/Firefox (ADR 0002).
- **Sentry stays optional and scrubs hard.** Source code and tokens must not leave (CLAUDE.md section 9). Sentry JS v11 collects user info, cookies and bodies by default, so the web init turns each off via `dataCollection`.
- **`DAILY_COST_CAP_USD=1`.** The only LLM spend brake; reviews degrade to `skipped`, not to an error.

## Free-tier limitations (honest list)

Cold starts of about a minute; a webhook that arrives while the instance sleeps can be lost (GitHub does not redeliver on its own; use *Redeliver*); a long deploy interrupts the worker; one 512 MB process for web and AI work; Supabase pauses idle projects; the uptime pinger uses nearly all 750 monthly Render hours; the demo is single-instance, so zero-downtime scaling is not demonstrated.

## 5 likely interview questions

1. **How do you run migrations safely when several instances start at once?** A Postgres session advisory lock on a dedicated connection around `alembic upgrade head`. The second instance waits, then sees nothing to apply. I verified Alembic takes no lock itself.
2. **What happens if a migration fails during deploy?** The container exits before uvicorn, so the health check never passes and Render keeps routing to the old instance. Rollback of code is a redeploy of the previous image; migrations are written to be backward compatible so old code still works on the new schema.
3. **Why do the browser's API calls go through a Vercel rewrite?** One origin means host-only SameSite=Lax cookies work in every browser and CORS is not involved. The cost is one extra hop and the caching risk, which `no-store` plus `x-vercel-enable-rewrite-caching: 0` handles.
4. **How do you know the deploy that the smoke test checks is the new one?** `/health` reports `RENDER_GIT_COMMIT`; the test waits for it to equal the tested SHA.
5. **How would you replace a leaked GitHub App private key without downtime?** Generate a second key (an App can hold several), update `GITHUB_APP_PRIVATE_KEY` in Render, verify with a test PR, then delete the old key.

## Things to try breaking

- Put a wrong `DATABASE_URL` in Render: the deploy should fail and the old version keep serving.
- Run `python -m app.db.migrate` twice at once against a scratch database; read the "waiting for the migration lock" log line.
- Remove `no-store` from `core/headers.py` and request `/api/me` twice through Vercel (do not do this in production).
- Set `DAILY_COST_CAP_USD=0` and open a PR: the run should be `skipped`.
- Stop the pinger for 20 minutes, then open a PR and watch the cold start.
