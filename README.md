# CodeLens

AI code review for GitHub pull requests, plus chat with your repo: grounded answers with `file:line` citations.

> **Status: under construction.** Milestones M0 (foundation), M1 (database layer), M2 (GitHub sign-in, sessions and RBAC) and M3 (GitHub App auth and webhook intake) are done; nothing is deployed yet. See the checklist below.

## Architecture

_Diagram placeholder: a mermaid architecture diagram arrives with the first deployed milestone (M7). The design is described in [CLAUDE.md](CLAUDE.md), section 4._

In one line: GitHub webhook → FastAPI (verify, record, enqueue, return `202`) → Postgres job queue → worker (LangGraph review pipeline) → review posted on the PR. A React dashboard and a streaming repo chat sit on top.

## Quickstart (local)

Requirements: Docker Desktop. For running tests outside Docker you also need Node 22+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/GOPIKA-BTECH23/codelens.git
cd codelens
cp .env.example .env   # local settings; gitignored
# edit .env: JWT_SECRET, GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, GITHUB_WEBHOOK_SECRET (see below)
docker compose up --build
```

Sign-in needs a GitHub App of your own (free, a few minutes): GitHub → Settings → Developer settings → GitHub Apps → New. Set the **callback URL** to `http://localhost:5173/api/auth/github/callback`, give it **Organization permissions → Members: Read-only**, then put its Client ID and a client secret in `.env`. Generate `JWT_SECRET` and `GITHUB_WEBHOOK_SECRET` with `openssl rand -hex 32` (the webhook secret must also be pasted into the App's settings). The API refuses to start without these.

Then open:

| What                 | URL                                |
| -------------------- | ---------------------------------- |
| Web app (sign in)    | http://localhost:5173              |
| API status page      | http://localhost:5173/health       |
| API health           | http://localhost:8000/health       |
| API readiness        | http://localhost:8000/ready        |
| API docs (Swagger)   | http://localhost:8000/docs         |
| Postgres + pgvector  | `localhost:5432`                   |

Always use the app through `http://localhost:5173`: the browser reaches the API at `/api/*` on the same origin, which is what makes the httpOnly session cookies work ([ADR 0002](docs/adr/0002-cookie-and-csrf-strategy.md)). See [.env.example](.env.example) for every variable.

Set up the database (the API container does not migrate for you yet):

```bash
cd apps/api
uv run alembic upgrade head                 # create the schema
ENVIRONMENT=development uv run python -m scripts.seed   # optional: fake dev data
```

If port 5432 is already used on your machine, set `POSTGRES_PORT` (for example `54329`) in the root `.env`, restart Postgres (`docker compose up -d postgres`), and use the same port in that file's `DATABASE_URL`.

### The worker

`docker compose up` also starts a `worker` service (`python -m app.worker.main`) that claims jobs from the Postgres queue. Follow it with `docker compose logs -f worker`; every line carries the `job_id` (and `run_id` for reviews).

**M4 note:** the `review_pr` and `index_repo` handlers are stubs. A PR moves `queued → running → succeeded`, but **no review is performed** (the run's `model` column says `m4-stub-no-review`); the real pipeline arrives in M5. Uninstall `purge_installation` jobs have no handler yet and end up `dead` on purpose ([ADR 0004](docs/adr/0004-worker-deployment-modes.md)).

| Mode | How | Good for | Trade-offs |
| ---- | --- | -------- | ---------- |
| Separate process | `python -m app.worker.main` or the compose `worker` service | local dev, any paid host | API and worker restart, scale and fail independently; needs a second process (Render's free tier has no free worker) |
| In the API process | `RUN_WORKER_IN_API=true` | free-tier demo with one service | shares RAM (512 MB) and the event loop with web traffic; a deploy or sleep stops both; cannot scale them apart |

### Receiving GitHub webhooks locally

GitHub must reach `POST /webhooks/github` on the API (port **8000**, not the web app). Use [ngrok](https://ngrok.com/) (a free account includes one dev domain):

```bash
ngrok http 8000
# copy the https://<your-dev-domain>.ngrok-free.app "Forwarding" URL, then in the GitHub App
# settings set Webhook URL = https://<your-dev-domain>.ngrok-free.app/webhooks/github
```

Use ngrok rather than smee.io: smee re-serialises the JSON body, which can change its bytes and break the signature check ([ADR 0003](docs/adr/0003-webhook-intake-and-github-client.md)). Without a tunnel, replay a saved, correctly signed payload:

```bash
cd apps/api
uv run python -m scripts.replay_webhook tests/fixtures/webhooks/pull_request.opened.json
```

Run the checks without Docker (the API tests need `docker compose up -d postgres`):

```bash
cd apps/api && uv sync && uv run ruff check . && uv run mypy && uv run pytest
cd apps/web && npm ci && npm run lint && npm run typecheck && npm test && npm run build
```

## Milestones

- [x] M0 Foundation and monorepo scaffolding
- [x] M1 Database layer
- [x] M2 Authentication, sessions and RBAC
- [x] M3 GitHub App integration and webhook intake
- [x] M4 Worker and job processing
- [ ] M5 LLM layer and the first agent
- [ ] M6 Frontend dashboard and review pages
- [ ] M7 Deploy the MVP
- [ ] M8 Repo indexing
- [ ] M9 Hybrid retrieval and streaming repo chat
- [ ] M10 LangGraph multi-agent review pipeline
- [ ] M11 Observability, cost control and rate limiting
- [ ] M12 Testing hardening and CI quality gates
- [ ] M13 Evaluation harness
- [ ] M14 Security hardening review
- [ ] M15 Polish: docs, demo, résumé

## License

MIT, see [LICENSE](LICENSE).
