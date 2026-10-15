# CodeLens — Project Brief for Claude Code

> Read this file fully at the start of every session. It is the source of truth for what we are building, how, and how we work together.

## 1. What CodeLens is

CodeLens is an AI code-review and repo-intelligence platform. It has two products on one platform:

1. **Automated PR review.** A GitHub App receives `pull_request` webhooks, runs a multi-agent LangGraph pipeline (security, style/quality, test-gap, summarizer) over the diff, and posts inline review comments plus a summary on the PR.
2. **Repo chat.** Repos are indexed (structure-aware code chunks + embeddings in pgvector). Users ask questions in a web UI and get streamed answers with file:line citations (hybrid retrieval: vector + full-text).

A React + TypeScript dashboard lets users sign in with GitHub, pick repos, browse reviews, give feedback on findings, chat with a repo, and (admins) see cost/latency/acceptance analytics.

## 2. Who I am and why this project exists

I am a final-year B.Tech CSE (AI/ML) student building this as my **solo flagship project** for job applications. Two consequences for how you work:

- **I must be able to explain and defend every part in interviews.** Never hand me magic. Explain design decisions, and keep code readable over clever.
- **It must be live, tested and honest.** Real deployment, real tests, real eval numbers. No fabricated metrics, no fake data presented as real.

## 3. Working agreement (how you must behave)

1. **One milestone at a time.** Only work on the milestone I name. Do not build ahead.
2. **Plan before coding.** For any non-trivial task, first reply with a short plan (files to create or change, key decisions, risks) and wait for my "go" unless I said to proceed.
3. **Small, reviewable commits** with Conventional Commit messages (`feat:`, `fix:`, `test:`, `chore:`, `docs:`, `ci:`). Never one giant commit. Never commit secrets, `.env`, private keys or build artifacts.
4. **Tests ship with features.** Every feature comes with tests in the same milestone. Run the tests and linters yourself and fix failures before saying "done".
5. **Explain as you go.** At the end of each milestone, write `docs/learning/Mxx-<name>.md`: what was built, the request/data flow in plain words, why each decision was made, 5 likely interview questions with short answers, and "things to try breaking". Then quiz me with 3 questions about the milestone.
6. **Verify, don't assume.** Library and API details change. Before using GitHub API endpoints, LangGraph, LangChain, Gemini/Groq SDKs, tree-sitter bindings or Supabase features, check the current official docs (use web search or fetch) instead of relying on memory. Pin versions you verified.
7. **Ask when blocked on a decision that is mine** (naming a paid service, deleting data, changing the architecture). Otherwise pick the sensible default, state it in one line, and continue.
8. **Tell me the manual steps.** Anything only I can do (create the GitHub App, paste secrets, create cloud accounts, click deploy buttons) goes in a clearly labelled "Manual steps for you" list with exact instructions. Never pretend to have done them.
9. **Do not touch** anything outside this repo, and never run destructive commands (dropping databases, `rm -rf` outside build dirs, force-push) without asking.

## 4. Architecture

```
GitHub ──webhook (HMAC-signed)──► FastAPI ──insert job──► Postgres `jobs` table
   ▲                                │                          │ SELECT … FOR UPDATE SKIP LOCKED
   │ post review (installation      │ REST + SSE               ▼
   │ token)                         ▼                     Worker process
   │                           React + TS (Vercel)        ├─ LangGraph review graph
   └─────────────────────────────────────────────────────┤  ├─ GitHub client (app auth)
                                                          │  └─ Indexer (tree-sitter → embeddings)
                          Supabase Postgres + pgvector ◄──┘
                          LLM provider (Gemini/Groq) · Langfuse · Sentry
```

**Golden rule:** the webhook handler never does AI work. It verifies the signature, records the delivery idempotently, enqueues a job, and returns `202` within seconds. The worker does the slow work.

**Queue decision:** use a **Postgres-backed job table** (`SKIP LOCKED`, attempts, backoff, visibility timeout) so the stack needs no extra paid or sleeping service. Keep the queue behind an interface (`JobQueue`) so Redis could replace it later. The worker can run as its own process (Docker/Render worker) or as a background loop inside the API process for free-tier demos (configurable via env var).

## 5. Tech stack (verify versions before pinning)

**Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x (async) + asyncpg, Alembic, httpx, PyJWT + cryptography, `uv` for dependency management, Ruff (lint + format), mypy (on core modules), pytest + pytest-asyncio + respx.

**AI layer:** LangGraph (agent orchestration), langchain-core (retrievers, splitters, messages), provider interface with Gemini and Groq implementations plus a `FakeLLM` for tests, Pydantic structured outputs, tool calling, tree-sitter for code chunking, Semgrep / Bandit / Ruff as grounding tools for the security and style agents, Langfuse for traces/cost/latency.

**Database:** Postgres (Supabase in production, `pgvector/pgvector` image locally) with the `vector` extension, HNSW index on embeddings, `tsvector` full-text column for lexical search.

**Frontend:** Node.js current LTS, 22 or newer (Node 20 reached end-of-life in April 2026; verify the LTS line before pinning), Vite, React 19, TypeScript (strict), React Router, TanStack Query, Tailwind CSS, Vitest + Testing Library, Playwright (one E2E flow), ESLint + Prettier.

**Auth and security:** GitHub OAuth for users, own JWT sessions in httpOnly cookies, RBAC, GitHub App JWT → installation token for the worker, webhook HMAC verification, rate limiting, prompt-injection defences, secrets only via env vars.

**DevOps:** Docker + docker-compose, GitHub Actions (CI on PR; CD on merge to `main`), Vercel (web), Render (API + worker), Supabase (DB), Sentry (errors), Langfuse Cloud (LLM tracing).

## 6. Repo layout (monorepo)

```
codelens/
├─ CLAUDE.md
├─ PROMPTS.md                  # milestone prompts (for me, not for you to edit)
├─ README.md
├─ docker-compose.yml
├─ .env.example                # every env var documented, no real values
├─ .github/workflows/          # ci.yml, deploy.yml, evals.yml
├─ apps/
│  ├─ api/
│  │  ├─ pyproject.toml
│  │  ├─ alembic.ini
│  │  ├─ migrations/
│  │  ├─ app/
│  │  │  ├─ main.py  config.py  deps.py
│  │  │  ├─ core/        # security.py, logging.py, errors.py, rate_limit.py
│  │  │  ├─ db/          # session.py, base.py, models/
│  │  │  ├─ api/         # auth.py me.py repos.py pulls.py findings.py chat.py analytics.py webhooks.py health.py
│  │  │  ├─ services/    # github_app.py github_client.py diff.py jobs.py indexing.py retrieval.py review_poster.py
│  │  │  ├─ ai/
│  │  │  │  ├─ llm/      # base.py gemini.py groq.py fake.py
│  │  │  │  ├─ graph/    # state.py build.py nodes/
│  │  │  │  ├─ prompts/  # versioned prompt files
│  │  │  │  ├─ tools/    # search_repo, semgrep, bandit, ruff wrappers
│  │  │  │  └─ schemas.py
│  │  │  └─ worker/      # main.py handlers.py
│  │  └─ tests/          # unit/, integration/, fixtures/
│  └─ web/
│     ├─ package.json  vite.config.ts  tailwind config
│     ├─ src/ (pages/, components/, api/, hooks/, lib/)
│     └─ e2e/            # Playwright
├─ evals/                  # dataset/, run_evals.py, results/
├─ docs/                   # architecture.md, adr/, learning/, api.md
└─ infra/                  # render.yaml, notes on deployment
```

## 7. Data model (Postgres)

`users`, `installations` (github_installation_id, account_login, account_type, uninstalled_at), `installation_members` (role: owner/admin/member), `repositories` (index_status, last_indexed_sha), `pull_requests`, `review_runs` (status, model, tokens_in/out, cost_usd, latency_ms, error, prompt_version), `findings` (file, line, severity, category, message, suggestion, confidence, user_feedback), `code_chunks` (repo_id, path, language, start_line, end_line, content, embedding vector, tsv, commit_sha), `chat_sessions` (repository_id CASCADE, user_id SET NULL), `chat_messages` (cited_chunk_ids), `webhook_deliveries` (delivery_id UNIQUE), `jobs` (type, payload, status, attempts, run_after, locked_at, last_error), `refresh_tokens` (hashed, rotated, revoked_at).

**Not part of M1:** `repository_access` (user_id, repository_id, UNIQUE pair; synced from GitHub at login) is created by the **M2** migration, together with the code that fills and enforces it (decision 13). M1 deliberately ships without it, because an empty table nothing enforces would suggest an authorization guarantee that does not exist yet.

**Authorization uses two levels:** installation membership gives the role, and `repository_access` decides which repos of that installation a user may see. Every repo-scoped endpoint checks both.

**Data retention on uninstall** (`installation.deleted`): set `installations.uninstalled_at` (the installation, repository and review rows are KEPT; foreign-key `ON DELETE CASCADE` exists for hard deletes only and is never triggered by an uninstall), stop the installation's jobs, then immediately hard-delete its `code_chunks`, `chat_sessions` and `chat_messages`. The purge job deletes only `code_chunks` and chat data. Keep review metadata (`pull_requests`, `review_runs`, finding file/line/severity/category/confidence/feedback). 30 days after uninstall, purge finding text that can contain code (`findings.message` and `findings.suggestion` set to NULL).

**Schema growth:** columns that later milestones need (for example `repositories.settings`, `review_runs.head_sha`, `findings.dismissed`, `code_chunks.symbol` / `content_hash`, per-agent usage) are added by that milestone's own migration, not up front.

All schema changes go through Alembic migrations. Never edit the DB by hand.

## 8. Core flows (must stay true)

**PR review:** webhook (`pull_request` opened/synchronize/reopened) → verify `X-Hub-Signature-256` → insert into `webhook_deliveries` (unique `X-GitHub-Delivery`; duplicate → 200 no-op) → create `review_runs(queued)` + job → 202 → worker claims job → app JWT → installation token → fetch diff → drop noise (lockfiles, generated, binaries, vendored) → LangGraph graph → validate findings against diff hunks (line must exist on the RIGHT side of a changed hunk) → drop duplicates and low-confidence → post ONE batched review (inline comments + summary) → save findings/cost/latency → mark run `succeeded`. New push to the same PR supersedes older queued/running runs.

**Indexing:** on installation or push → fetch changed files → tree-sitter chunks (function/class level with path, language, line range; fallback to windowed text) → embed in batches → upsert into `code_chunks` → update `last_indexed_sha`.

**Chat:** question → query rewrite → hybrid retrieval (pgvector cosine + Postgres full-text, merged with reciprocal rank fusion) → grounded prompt → streamed SSE answer with citations like `path:start-end` → persist messages and cited chunks. If retrieval finds nothing relevant, say so instead of guessing.

**LangGraph review graph:** `fetch_context → triage → [security_agent ‖ style_agent ‖ test_gap_agent] → merge_dedupe → rank → summarizer → output`. Agents return Pydantic `Finding` objects. Invalid structured output triggers a bounded retry edge. Large PRs are chunked per file group and merged. Agents have read-only tools (`search_repo`, static analyzers); they can never write to GitHub. Only our code posts comments.

## 9. Security rules (non-negotiable)

- Webhook: constant-time HMAC compare, reject on mismatch, idempotent on delivery ID.
- Two separate auth systems: user auth (OAuth → JWT cookie → RBAC) and app auth (private key → app JWT → installation token, worker only).
- Cookies: httpOnly, Secure, SameSite set deliberately; state-changing requests protected against CSRF (SameSite plus required custom header or double-submit token). CORS locked to the web origin with credentials.
- Refresh tokens: random, stored hashed, rotated on use, revocable.
- Never log secrets, tokens, or source code content. Encrypt anything sensitive at rest. Redact in logs and traces.
- **Prompt injection:** PR diffs and repo files are untrusted data. Wrap them in clear delimiters, instruct the model to treat them only as data, give agents read-only tools, validate every output against a schema and against the diff, and never let model output trigger an action beyond posting validated comments.
- Authorization checks on every endpoint: a user can only see repos of installations they belong to. Write a test for each role boundary.
- Per-installation rate limits and a daily LLM cost cap that halts reviews gracefully.
- No secrets in the repo, ever. `.env.example` documents variables; real values live in provider dashboards.

## 10. Code conventions

- **Python:** typed, async I/O, Pydantic models at every boundary, no business logic in route handlers (routes → services). Small functions, docstrings on public service functions. Ruff clean.
- **TypeScript:** `strict`, no `any` without a comment, API types in one place, server state via TanStack Query only, small components.
- **Errors:** typed exceptions in `core/errors.py`, one global handler, consistent JSON error shape `{ "error": { "code", "message" } }`.
- **Logging:** structured JSON logs with request ID and run ID.
- **Config:** one `Settings` class (pydantic-settings); app fails fast on missing required env vars.
- **LLM calls:** only through the provider interface; every call records model, tokens, cost, latency, prompt version. Prompts live in versioned files, not inline strings.
- **Tests:** unit tests for pure logic (diff mapping, chunking, RBAC, fusion), integration tests with mocked GitHub (`respx`) and `FakeLLM`, real Postgres via docker-compose, one Playwright E2E. Aim for meaningful coverage, not a vanity number; report the real number.

## 11. Environment variables (document all in `.env.example`)

`DATABASE_URL`, `JWT_SECRET`, `JWT_ACCESS_TTL_MINUTES`, `JWT_REFRESH_TTL_DAYS`, `WEB_ORIGIN`, `API_ORIGIN`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (PEM, base64 or path), `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER` (gemini/fake), `GEMINI_API_KEY`, `GROQ_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `SENTRY_DSN`, `RUN_WORKER_IN_API` (true/false), `DAILY_COST_CAP_USD`, `LOG_LEVEL`, `ENVIRONMENT` (development/test/production; defaults to production; the seed script needs development; cookies are `Secure` unless development/test), `AUTH_RATE_LIMIT_PER_MINUTE` (optional, default 20).

Required from M2: `JWT_SECRET` (32+ characters, placeholders rejected), `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`. The GitHub App callback URL is `WEB_ORIGIN/api/auth/github/callback` (ADR 0002).

## 12. Common commands (keep this section updated as the repo grows)

- Start local stack: `docker compose up --build` (web :5173, api :8000, postgres :5432). Stop: `docker compose down` (add `-v` to also delete the DB volume). After changing `apps/web/package.json`, rebuild with `docker compose up --build -V` so the container's `node_modules` volume is recreated.
- First-time local setup: `cp .env.example .env` (the root `.env` is gitignored), then fill in `JWT_SECRET`, `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` (the API and `docker compose` refuse to start without them). It supplies `DATABASE_URL` (localhost + the published port), `WEB_ORIGIN`, `ENVIRONMENT=development` and `POSTGRES_PORT`; every command below, run from `apps/api`, reads it (alembic, pytest, seed and uvicorn need no exported variables). Docker compose reads the same file only for `${...}` substitution; the api container gets its own in-network `DATABASE_URL` (`@postgres:5432`) from `docker-compose.yml`, so both setups coexist. Real environment variables override `.env`.
- API install: `cd apps/api && uv sync`
- API tests: `cd apps/api && uv run pytest`
- API lint/format: `cd apps/api && uv run ruff check . && uv run ruff format .` (CI runs `ruff format --check .`)
- API without Docker: `cd apps/api && uv run uvicorn --factory app.main:create_app --reload` (needs `DATABASE_URL` and `WEB_ORIGIN` from the root `.env` or the environment)
- Migrations (from M1): `cd apps/api && uv run alembic upgrade head` / `uv run alembic revision --autogenerate -m "msg"`. Review autogenerated migrations by hand (pgvector, partial and generated-column pieces need care). A test fails if models and migrations drift.
- API tests need Postgres: `docker compose up -d postgres` first. `codelens_test` is created by `infra/postgres/init/` on a fresh volume (older volumes: the test run creates it). If port 5432 is taken on your machine, set `POSTGRES_PORT` (e.g. `54329`) in the root `.env` and use the same port in its `DATABASE_URL`; the tests pick the port up from `.env` (or set `TEST_DATABASE_URL` to override).
- API types: `cd apps/api && uv run mypy` (strict on `app/core/`, `app/services/` and `app/deps.py`; `ai/` joins when it is filled)
- Sign in locally: open `http://localhost:5173` (never `:8000` directly; cookies live on the web origin) and use "Sign in with GitHub". Session state: `GET /api/me`.
- Seed fake dev data (needs `ENVIRONMENT=development` and a localhost DB): `cd apps/api && uv run python -m scripts.seed`
- Health: `GET /health` (liveness, always 200, reports `database` and `revision`) and `GET /ready` (503 when the DB is down)
- Web install: `cd apps/web && npm ci`
- Web dev: `npm run dev` · lint: `npm run lint` · format: `npm run format` (check: `npm run format:check`) · types: `npm run typecheck` · tests: `npm test` · build: `npm run build`
- Web E2E (from M12): `npx playwright test`
- Workflow lint: `docker run --rm -v "$(pwd):/repo" -w /repo rhysd/actionlint:latest`
- Webhook tunnel for local GitHub testing: smee.io or ngrok, pointed at `POST /webhooks/github`

## 13. Definition of done (per milestone)

1. Code works locally via docker-compose.
2. Tests written and passing; linters clean; CI green.
3. Migrations included if the schema changed.
4. `.env.example`, `README`, and the Commands section updated if needed.
5. `docs/learning/Mxx-*.md` written, and I have been quizzed.
6. A short summary from you: what changed, what I must do manually, what is next.

## 14. Scope control

- **MVP:** OAuth login, webhook intake, job queue, one security agent, review posted on the PR, basic dashboard, CI, deployed live.
- **V2:** repo indexing and chat, style and test-gap agents, full LangGraph fan-out, Langfuse.
- **Stretch:** eval harness with published numbers, static-analyzer tool grounding, feedback-driven prompt tuning, "suggest fix" follow-up PR.

If something is not in the milestone I named, do not build it. Suggest it instead.

## 15. Decisions log

Kickoff decisions, 2026-10-15. "Accepted" means the proposed default stands. Free-tier figures are still to be verified against provider docs in the milestone that uses them.

1. **Cookies across Vercel/Render:** Vercel rewrite `/api/*` → Render, host-only `HttpOnly; Secure; SameSite=Lax` cookies. **Decided:** try the rewrite first; if SSE is buffered, serve the built React app from FastAPI (single origin); a paid custom domain only after that.
2. **Render cold start vs webhooks:** GitHub waits ~10 s and does not auto-redeliver; use a free uptime pinger on `/health` and document the limitation. Accepted.
3. **No free Render worker:** `RUN_WORKER_IN_API=true` in production with one uvicorn process; separate worker process locally. Accepted.
4. **Supabase connection:** use the session-pooler URL (transaction pooler breaks asyncpg prepared statements; direct URL may be IPv6-only); pinger avoids project pausing; cap chunks per repo for the 500 MB tier. Accepted.
5. **512 MB RAM:** stream tarballs to disk, process files one at a time; Bandit + Ruff first, Semgrep optional. Accepted.
6. **Embedding provider:** separate `EMBEDDING_PROVIDER` (gemini/fake) from `LLM_PROVIDER`, since Groq likely has no embeddings API. Accepted.
7. **`EMBEDDING_DIM`:** 768 via the Gemini model's reduced output dimension, one constant checked at startup; changing it means migration + full re-index. Accepted.
8. **Cost on free tiers:** record "estimated cost at list price" from a dated pricing table, always labelled as an estimate. Accepted.
9. **Privacy:** README states the demo is for public/demo repos; no code sent to Langfuse by default. Accepted.
10. **Provider rate limits:** client-side limiter in the provider; resumable, low-concurrency eval runner. Accepted.
11. **App vs OAuth App:** GitHub App's own client ID/secret for user login; two apps, `CodeLens-dev` and `CodeLens`. Accepted.
12. **Role mapping:** personal-account installer = owner; org owners = admin, other org members = member; derived at login. Accepted.
13. **Repo-level access gap:** **Decided:** add `repository_access` in the M2 migration and enforce it on every repo-scoped endpoint.
14. **Stale membership:** sessions last at most 7 days; re-login re-syncs; `installation_repositories` webhooks handle removals. Accepted.
15. **Large diffs:** use paginated "list PR files"; files with no patch are skipped and named in the summary. Accepted.
16. **Stale posts:** comment with `line` + `side=RIGHT` + run `commit_id`; re-check supersede status right before posting. Accepted.
17. **Schema gaps:** later-milestone columns arrive in that milestone's migration. Accepted.
18. **Uninstall data:** **Decided:** uninstall sets `installations.uninstalled_at` and deletes nothing by cascade; the purge job hard-deletes only `code_chunks` and chat sessions/messages immediately; keep review metadata; purge finding text containing code after 30 days (see section 7 and ADR 0001).
19. **Encryption at rest:** store no GitHub tokens, hash refresh tokens, rely on Supabase disk encryption for chunk text (needed in plaintext for full-text search). Accepted.
20. **Node version:** current Node LTS (22+); Python stays 3.12 for wheel support. Accepted.
21. **Windows dev:** `.gitattributes` with `eol=lf`; analyzers run only in Docker; helper scripts in Python, not bash. Accepted.
22. **Misc:** rate limiter stored in Postgres behind an interface; mypy strict on `core/`, `services/`, `ai/`; tests use a separate `codelens_test` DB with per-test rollback. Accepted.
