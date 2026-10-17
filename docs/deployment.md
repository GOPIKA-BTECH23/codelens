# Deploying CodeLens (free tiers)

Production layout, names chosen for M7:

| Piece | Service | Name | URL |
| --- | --- | --- | --- |
| Web (React) | Vercel (Hobby) | `codelens-gopika` | `https://codelens-gopika.vercel.app` |
| API + worker | Render (Free web service, Docker) | `codelens-api-gopika` | `https://codelens-api-gopika.onrender.com` |
| Database | Supabase (Free) | `codelens` | session-pooler URL |
| CI/CD | GitHub Actions | `CI`, `Deploy` | |

If Vercel gives you a different domain (the name was free on 2026-10-17 but is only reserved when you create the project), run the **find-and-replace list** at the end of this file.

```
browser ──► Vercel (static React app)
              └─ /api/*  ──rewrite──►  Render: FastAPI + in-process worker ──► Supabase Postgres
GitHub ──webhook (signed)────────────►  Render  (direct, NOT through Vercel; ADR 0003)
```

## What happens on a deploy

1. A PR is merged to `main`. **CI** runs (lint, types, tests, build for API and web).
2. Only if CI ends `success`, the **Deploy** workflow starts (`workflow_run`). Render's and Vercel's own Git auto-deploys for `main` are switched off, so nothing else can deploy.
3. It calls the **Render deploy hook** with `&ref=<the commit CI tested>`. Render builds `apps/api/Dockerfile`.
4. The container starts with: `python -m app.db.migrate` (migrations under a Postgres advisory lock, see below), then `uvicorn`. Render waits for `GET /health` to answer before routing traffic to the new instance, then stops the old one (SIGTERM; the in-API worker drains running jobs for up to 20 s and releases the rest to the queue).
5. It calls the **Vercel deploy hook**, which builds and publishes `main`.
6. **Smoke tests:** poll `API_URL/health` until it reports *this commit* and `database: ok` (so the old instance answering cannot fake a pass), then check `WEB_URL/` and `WEB_URL/api/health` (the rewrite) both return 200.

Why the commit check matters: `/health` returns `commit` from Render's `RENDER_GIT_COMMIT`.

### Migrations at deploy time

- The start command runs `python -m app.db.migrate` before uvicorn. If a migration fails, the container exits, the health check never passes, and **Render keeps the previous instance serving**. A bad migration therefore blocks the deploy; it does not take the site down.
- **Alembic takes no lock itself** (checked in the 1.20.0 source: no advisory-lock call; the docs do not cover concurrent runs). Two instances starting together (a deploy overlapping a restart) could both try to apply the same revision. `app/db/migrate.py` takes `pg_advisory_lock` on a separate connection first; the second instance waits and then finds nothing to do. Tested in `tests/integration/test_migrate_lock.py` (a waiting migration, and two simultaneous runs).
- The lock is a **session** lock, so it needs a session-level connection: Supabase's **session pooler** (port 5432) or the direct URL. The transaction pooler (port 6543) would break it (and asyncpg prepared statements, decision 4). Use the session pooler.
- Why not Render's `preDeployCommand`? Its docs give no plan restrictions, but it is not confirmed for Free instances. A start command works on every plan.
- **Make every migration backward compatible with the previous release** (add columns nullable or with defaults, drop columns only one release after the code stopped using them), because during a deploy the old code runs against the new schema for a moment, and because rollback (below) restores old code over a newer schema.

### Rolling back

1. **Code only (the usual case):** Render dashboard → service → *Events* → find the last good deploy → *Rollback*. This redeploys the old image. Because migrations run on start, a rolled-back image whose code is older than the schema keeps working **only if the migration was backward compatible** (rule above). Rolling back does *not* undo migrations.
2. **Schema:** only if the last migration must be undone. From your machine, against the production URL (session pooler): `cd apps/api && DATABASE_URL=<prod url> uv run alembic downgrade -1`. Roll code back **first**, then the schema. Check the migration's `downgrade()` by reading it; some drop data.
3. **Web:** Vercel → Deployments → previous deployment → *Promote to Production*.
4. Later pushes to `main` deploy again as usual, so fix forward quickly or revert the commit.

## Environment variables: which service needs which

Legend: **R** = Render (`codelens-api-gopika`), **V** = Vercel build, **GA** = GitHub Actions. "Secret" means it goes only in that dashboard, never in the repo.

### Render (API + in-process worker)

Set by `infra/render.yaml`: the rows with a fixed value are in the file; the secrets are asked once when you create the Blueprint (or add them in the Environment tab).

| Variable | Value / where it comes from | Secret |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | no |
| `RUN_WORKER_IN_API` | `true` (no free Render worker; ADR 0004) | no |
| `WORKER_CONCURRENCY` | `1` (512 MB RAM shared with web traffic) | no |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `3` / `2` (small, the Supabase free pooler allows few clients) | no |
| `DAILY_COST_CAP_USD` | **`1`**. Reviews are marked `skipped` once today's estimated spend reaches it. Raise it deliberately. | no |
| `LLM_PROVIDER` | `groq` (`fake` is refused in production) | no |
| `WEB_ORIGIN` | `https://codelens-gopika.vercel.app` (cookies, CORS, CSRF origin check, OAuth callback) | no |
| `API_ORIGIN` | `https://codelens-api-gopika.onrender.com` | no |
| `LOG_LEVEL` | `INFO` | no |
| `DATABASE_URL` | Supabase → *Connect* → **Session pooler** string, rewritten as `postgresql+asyncpg://postgres.<ref>:<URL-encoded password>@<pooler-host>:5432/postgres` | **yes** |
| `JWT_SECRET` | generated by Render (`generateValue`). Changing it logs everyone out. | **yes** |
| `GITHUB_OAUTH_CLIENT_ID` | production App "CodeLens" → Client ID (also the App JWT issuer, ADR 0003) | no |
| `GITHUB_OAUTH_CLIENT_SECRET` | production App → *Generate a new client secret* | **yes** |
| `GITHUB_WEBHOOK_SECRET` | new `openssl rand -hex 32`; the same string goes in the App's webhook settings | **yes** |
| `GITHUB_APP_PRIVATE_KEY` | the App's `.pem`, base64-encoded on one line (see below) | **yes** |
| `GROQ_API_KEY` | console.groq.com → API keys | **yes** |
| `SENTRY_DSN` | leave empty: Sentry stays off | no |
| `RENDER_GIT_COMMIT` | set by Render itself; shown in `/health` | no |
| `DEBUG_SSE_ENABLED` | **temporary**: `true` only while running the streaming check below; delete afterwards | no |

Not needed in production: `GEMINI_API_KEY`, `EMBEDDING_*` (M8), `LANGFUSE_*` (M11), `POSTGRES_*` (compose only).

Base64 of the key, on one line: PowerShell `[Convert]::ToBase64String([IO.File]::ReadAllBytes("codelens.private-key.pem"))`; Git Bash `base64 -w0 codelens.private-key.pem`.

### Vercel (web)

| Variable | Needed? | Notes |
| --- | --- | --- |
| `VITE_SENTRY_DSN` | no | unset = Sentry is not even downloaded. A DSN is public by design. |

Everything else is in `apps/web/vercel.json` (rewrite to Render, no rewrite caching, security headers, auto-deploy off for `main`). The Render URL inside it is not a secret.

### GitHub Actions (repo → Settings → Secrets and variables → Actions)

| Name | Kind | Value |
| --- | --- | --- |
| `RENDER_DEPLOY_HOOK_URL` | secret | Render → service → Settings → *Deploy Hook* (the URL contains a key) |
| `VERCEL_DEPLOY_HOOK_URL` | secret | Vercel → project → Settings → Git → *Deploy Hooks* → name `deploy`, branch `main` |
| `API_URL` | variable | `https://codelens-api-gopika.onrender.com` |
| `WEB_URL` | variable | `https://codelens-gopika.vercel.app` |

The Render docs state hooks work with `GET`/`POST` and a `key` query parameter and list no plan restriction; **that it works on the Free plan is not confirmed by the docs**, so the first deploy is the test (manual step 8). Vercel documents deploy hooks for Hobby (5 per project, 60 triggers per hour).

## Production GitHub App "CodeLens"

The dev App (`CodeLens-dev`) points at ngrok and localhost, so production needs its own (decision 11). Create it at GitHub → Settings → Developer settings → GitHub Apps → New:

| Setting | Value |
| --- | --- |
| Name | `CodeLens` (if taken, any name; it only appears in the UI) |
| Homepage URL | `https://codelens-gopika.vercel.app` |
| Callback URL | `https://codelens-gopika.vercel.app/api/auth/github/callback` (the **web** origin, ADR 0002) |
| Expire user authorization tokens | on |
| Request user authorization (OAuth) during installation | on |
| Setup URL | `https://codelens-gopika.vercel.app` |
| Webhook: active | yes |
| Webhook URL | `https://codelens-api-gopika.onrender.com/webhooks/github` (the **Render** URL, never Vercel: a proxy could re-serialise the body and break the signature) |
| Webhook secret | the new `GITHUB_WEBHOOK_SECRET` |
| Repository permissions | **Contents: Read-only**, **Metadata: Read-only**, **Pull requests: Read and write**. Nothing else. |
| Organization / account permissions | none for now (see below) |
| Subscribe to events | **Pull request**, **Installation**, **Installation repositories** only. No Push until M8. |
| Where can this GitHub App be installed? | **Only on this account** |

Why these permissions are enough (checked against the code): the worker downloads the PR's files with Contents/Metadata, posts the review with Pull requests: write (the fallback summary comment goes through the same permission), login reads `/user` and `/user/installations*`. There is no Issues call and no email read, so neither permission is requested. `Installation` and `Installation repositories` events need no extra permission.

Then: *Generate a private key* (downloads a `.pem`), *Generate a new client secret*, copy the Client ID.

### Opening the App to other accounts later

"Only on this account" means only you can install it, which is what a portfolio demo needs while the LLM bill is yours. Switching to "Any account" (App settings → *Advanced* → *Make public*) lets anyone install it. Do it only after:

1. **Cost:** `DAILY_COST_CAP_USD` is the only brake on LLM spend. Keep it low (`1`) and remember it is a global daily cap: one noisy stranger can use up the day's budget for everyone. A per-installation cap/rate limit is M11.
2. **Public repos only:** the diff of every reviewed PR goes to the LLM provider (README, privacy). State that the demo is for public/demo repositories. There is no per-repo opt-in yet, so a stranger installing on a private repo would send private code to Groq.
3. **Org installs:** organisation installs need the **Organization permission "Members: Read-only"**, otherwise `GET /user/memberships/orgs/{org}` returns 403 and roles cannot be derived (decision 12). Not needed while only your personal account installs.
4. **Abuse:** the free Render instance has 512 MB. Many installs mean many PRs; the in-API worker is one process.
5. Uninstall purge (`purge_installation`) still has no handler (ADR 0004). Public installs create data-deletion obligations, so write it first.

## Free-tier limits you will notice

| Service | Limit | What you see |
| --- | --- | --- |
| Render Free web service | sleeps after **15 min** without inbound traffic; wake-up takes about **1 minute**; **750** instance hours per month per workspace, then suspended until next month; **0.1 CPU, 512 MB RAM**; no free background workers | first request after idle is slow ("first load may take ~1 minute"); a **webhook arriving while asleep may be lost**, GitHub does not auto-redeliver; the worker also sleeps |
| Supabase Free | **500 MB** database, **5 GB** egress, **2** free projects. Projects inactive for a while are **paused** (the docs page I checked did not give the number of days; check the dashboard) | keep the project alive with the pinger; a paused project makes `/health` report `database: unavailable` |
| Vercel Hobby | personal, non-commercial use; 5 deploy hooks; 60 hook triggers per hour | none in normal use |
| Groq | rate limits per model (`LLM_REQUESTS_PER_MINUTE` limits us client-side) | 429s are retried, then the run fails visibly |

### Keep it awake: free uptime pinger

An uptime monitor (for example UptimeRobot's free plan, 5-minute interval) calling `https://codelens-api-gopika.onrender.com/health` keeps the instance awake and touches the database (which also counts as activity for Supabase). The cost: 750 hours per month is about 31 days of 24 h, so **one always-awake service uses roughly 744 h** and fits only because this is the only Render service in the workspace. Without the pinger, expect cold starts and missed webhooks; with it, a missed webhook can still happen during a deploy (a few seconds). **If a review is missing:** GitHub App settings → *Advanced* → *Recent deliveries* → *Redeliver*. The delivery ID is idempotent, so redelivering a delivery that did arrive is a safe no-op.

## Rotating secrets

| Secret | How | Effect |
| --- | --- | --- |
| `JWT_SECRET` | change in Render, redeploy | everyone is signed out |
| `GITHUB_WEBHOOK_SECRET` | change in the App settings **and** Render at about the same time | deliveries in between fail with 401 (redeliver them) |
| `GITHUB_OAUTH_CLIENT_SECRET` | App settings → generate a second secret, put it in Render, redeploy, then delete the old one | none if done in that order (the App allows two at once) |
| `GROQ_API_KEY` | create new key, change in Render, delete the old key | a running review may fail once and retry |
| Deploy hooks | Render: *Regenerate Hook*; Vercel: delete and recreate; then update the GitHub secret | deploys fail until updated |
| `DATABASE_URL` password | Supabase → Database settings → reset password; update in Render | connections drop until the redeploy; do both in a minute |

### Replacing the GitHub App private key

Use this when the key leaked, was lost, or you are rotating on schedule. An App can hold **several keys at once**, so there is no downtime if you go in this order:

1. App settings → *Private keys* → **Generate a private key**. A new `.pem` downloads. Keep the old one for now.
2. Base64-encode the new file onto one line (commands above).
3. Render → service → Environment → replace `GITHUB_APP_PRIVATE_KEY` → save (this redeploys). The worker parses the key at startup, so a malformed key stops the new instance from starting and Render keeps the old one running.
4. Verify: open a test PR (or *Redeliver* a `pull_request` delivery) and see a review appear.
5. Only then delete the old key in the App settings (*Delete* next to its fingerprint). If the old key leaked, do this as soon as step 3 is healthy.
6. Delete the local `.pem` files you no longer need and never commit them (`.gitignore` blocks `.env*`, not `.pem`: keep keys outside the repo).

## Sentry (optional, off by default)

Without `SENTRY_DSN` / `VITE_SENTRY_DSN` nothing is sent and the SDK is not loaded in the browser. When you create a Sentry account later, set the DSNs. Scrubbing is on regardless: no cookies, headers, request bodies, query strings, user info, breadcrumbs or stack-frame variables leave the process (`app/core/sentry.py`, `apps/web/src/lib/sentry.ts`, both unit tested). Not done: **source maps** for the web build (needs a Sentry account and an auth token; add `@sentry/vite-plugin` with `SENTRY_AUTH_TOKEN` as a Vercel build variable when you have one).

## Proxy headers and the client IP

Behind Vercel and Render the TCP peer is a proxy. The container starts uvicorn with `--proxy-headers --forwarded-allow-ips='*'`, so `request.client.host` (used for the `/auth/*` rate limit) becomes the first `X-Forwarded-For` entry. Vercel documents that it sets this header to the visitor's IP for rewrites. Accepted limitation: someone calling the Render URL directly can forge the header and dodge the per-IP limit on `/auth/*`; the secret-header option from Vercel's docs ("restricting your origin to Vercel traffic") is a future hardening step. The webhook does not depend on the client IP.

## Checklist before you call it live

- [ ] Supabase project created in the same region as Render (`singapore` in `render.yaml`); session-pooler URL copied
- [ ] Production GitHub App created with the settings above; key, client secret, webhook secret saved
- [ ] Render Blueprint created; `/health` shows `database: ok` and a `commit`
- [ ] Vercel project `codelens-gopika` created from this repo (Root Directory `apps/web`)
- [ ] GitHub Actions secrets and variables set
- [ ] `uv run python -m scripts.check_deployment https://codelens-gopika.vercel.app` passes (needs `DEBUG_SSE_ENABLED=true` once)
- [ ] `DEBUG_SSE_ENABLED` removed from Render, results pasted into ADR 0002
- [ ] Uptime pinger added
- [ ] Demo repo has the App installed; a PR got a review in GitHub and in the dashboard

## Find-and-replace list (if a name was taken)

| Placeholder | Appears in |
| --- | --- |
| `codelens-gopika.vercel.app` | `infra/render.yaml` (`WEB_ORIGIN`), `README.md`, this file, the GitHub App settings, `API`-side Actions variable `WEB_URL` |
| `codelens-api-gopika.onrender.com` | `apps/web/vercel.json` (rewrite destination), `infra/render.yaml` (`API_ORIGIN`), `README.md`, this file, the App's webhook URL, Actions variable `API_URL` |
| `codelens-api-gopika` (service name) | `infra/render.yaml` |
| `codelens-gopika` (project name) | this file and the learning notes only; the Vercel project name is set in the dashboard |
