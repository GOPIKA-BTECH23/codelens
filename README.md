# CodeLens

AI code review for GitHub pull requests, plus chat with your repo: grounded answers with `file:line` citations.

> **Status: under construction.** Milestone M0 (foundation) is in review; nothing is deployed yet. See the checklist below.

## Architecture

_Diagram placeholder: a mermaid architecture diagram arrives with the first deployed milestone (M7). The design is described in [CLAUDE.md](CLAUDE.md), section 4._

In one line: GitHub webhook → FastAPI (verify, record, enqueue, return `202`) → Postgres job queue → worker (LangGraph review pipeline) → review posted on the PR. A React dashboard and a streaming repo chat sit on top.

## Quickstart (local)

Requirements: Docker Desktop. For running tests outside Docker you also need Node 22+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/GOPIKA-BTECH23/codelens.git
cd codelens
docker compose up --build
```

Then open:

| What                 | URL                          |
| -------------------- | ---------------------------- |
| Web app              | http://localhost:5173        |
| API health           | http://localhost:8000/health |
| API docs (Swagger)   | http://localhost:8000/docs   |
| Postgres + pgvector  | `localhost:5432`             |

The web page shows the live `/health` response from the API. Defaults work with no `.env`; see [.env.example](.env.example) for every variable the project will eventually use.

Run the checks without Docker:

```bash
cd apps/api && uv sync && uv run ruff check . && uv run pytest
cd apps/web && npm ci && npm run lint && npm run typecheck && npm test && npm run build
```

## Milestones

- [ ] M0 Foundation and monorepo scaffolding
- [ ] M1 Database layer
- [ ] M2 Authentication, sessions and RBAC
- [ ] M3 GitHub App integration and webhook intake
- [ ] M4 Worker and job processing
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
