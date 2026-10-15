# M1: Database layer

## What was built

- **Async SQLAlchemy 2 + asyncpg**: an engine, a session factory and a `get_session` FastAPI dependency (`app/db/session.py`, `app/deps.py`).
- **13 tables** as models in `app/db/models/` (users, installations, installation_members, repositories, pull_requests, review_runs, findings, code_chunks, chat_sessions, chat_messages, webhook_deliveries, jobs, refresh_tokens). `repository_access` is intentionally M2 (ADR 0001).
- **Alembic (async)** with one hand-reviewed initial migration: the `vector` extension, all tables, an HNSW index on the embedding and a GIN index on a generated `tsvector` column.
- **A Postgres job queue** (`app/services/jobs.py`): `enqueue`, `claim_next` (`SKIP LOCKED`), `mark_succeeded`, `mark_failed` (exponential backoff, then `dead`), `recover_stale_locks`.
- **A fake-data seed script** (`python -m scripts.seed`) that refuses to run unless `ENVIRONMENT=development` and the DB host is localhost.
- **`/health`** (liveness, always 200, reports `database` and `revision`) and **`/ready`** (503 when the DB is down).
- **mypy strict on `app/core/`**, run in CI.
- **59 tests**: 39 integration tests against a real Postgres, 20 unit tests.

## Data model

```mermaid
erDiagram
    users ||--o{ installation_members : "belongs via"
    installations ||--o{ installation_members : has
    users ||--o{ refresh_tokens : owns
    installations ||--o{ repositories : has
    repositories ||--o{ pull_requests : has
    pull_requests ||--o{ review_runs : has
    review_runs ||--o{ findings : produces
    repositories ||--o{ code_chunks : "is indexed into"
    repositories ||--o{ chat_sessions : has
    users |o--o{ chat_sessions : "starts (SET NULL)"
    chat_sessions ||--o{ chat_messages : contains

    users { uuid id PK; bigint github_id UK; text login }
    installations { uuid id PK; bigint github_installation_id UK; timestamptz uninstalled_at }
    installation_members { uuid installation_id FK; uuid user_id FK; text role }
    repositories { uuid id PK; bigint github_repo_id UK; text index_status; text last_indexed_sha }
    pull_requests { uuid id PK; int number "UK with repository_id" }
    review_runs { uuid id PK; text status; numeric cost_usd; int latency_ms }
    findings { uuid id PK; text file; int line; text message "nullable, purged later" }
    code_chunks { uuid id PK; vector_768 embedding; tsvector tsv "generated" }
    chat_sessions { uuid id PK; uuid user_id "nullable" }
    chat_messages { uuid id PK; uuid_array cited_chunk_ids }
    webhook_deliveries { uuid id PK; text delivery_id UK }
    jobs { uuid id PK; text status; int attempts; timestamptz run_after; timestamptz locked_at }
    refresh_tokens { uuid id PK; text token_hash UK; timestamptz revoked_at }
```

`webhook_deliveries` and `jobs` are not linked by foreign keys: a delivery and its job are created in one transaction by code (M3), not by a constraint.

## How a request uses the database

1. A request arrives; the `SessionDep` dependency opens **one** `AsyncSession` from the factory.
2. The route calls a service; the service uses the session (`add`, `flush`, queries) but **never commits**.
3. When the route function returns, `get_session` commits. If anything raised, it rolls back. Commit/rollback exists in this one function.
4. `scope="function"` makes that commit run **before** the response is sent. Otherwise a failed commit would reach the client after it had already seen `200 OK`.

The job queue is the exception on purpose: its claim/complete/fail methods open their **own short transactions** from the session factory, because a worker is not inside a request.

## The queue, step by step

```
enqueue ──► queued ──claim_next──► running ──mark_succeeded──► succeeded
              ▲                      │
              │ backoff passes       ├─ mark_failed (attempts left) ─► queued, run_after = now + base·2^(n-1)
              └──────────────────────┤
                                     ├─ mark_failed (no attempts left) ─► dead
                                     └─ worker died: lock older than visibility timeout
                                        ─► recover_stale_locks ─► queued (or dead)
```

- `claim_next` is a short transaction: `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`, set `status='running'`, `locked_at=now()`, `attempts+1`, **commit**. The handler (which may take minutes) then runs with no transaction open.
- `attempts` is counted at claim time. A job that crashes its worker every time still runs out of attempts and goes `dead`; otherwise it would loop forever.
- `mark_succeeded` / `mark_failed` check `status='running' AND attempts=<mine>`. If a slow worker lost its lock and someone else re-claimed the job, the slow worker's late result changes nothing and the method reports it.
- Time comparisons use the database's `now()`, never the worker's clock.

## Why each decision was made

| Decision | Reason |
| --- | --- |
| Postgres queue instead of Redis | No extra paid or sleeping service on free tiers; `enqueue` can join the caller's transaction, so "delivery recorded" and "job enqueued" cannot get out of sync. Trade-off: lower throughput than Redis, polling instead of push. Hidden behind `JobQueue`, so it can be swapped. |
| `claim_next` commits before the handler runs | Holding a transaction open for a long LLM call would pin a connection and a row lock. The `running` status is the lease. |
| `VARCHAR` + `CHECK` instead of Postgres enums | Adding a value is a one-line migration; native enums are hard to change. |
| UUID keys | Not guessable, can be created anywhere. GitHub ids kept as unique `BIGINT` columns. |
| Naming convention on constraints | Alembic can only drop a constraint it knows the name of; unnamed constraints make downgrades fragile. |
| `EMBEDDING_DIM` is a constant, checked at startup | The column size is part of the schema; a mismatching setting should fail loudly at boot, not mid-indexing. |
| Uninstall sets `uninstalled_at`, deletes nothing by cascade | We promised to keep review metadata. Cascades are for hard deletes only (ADR 0001). |
| Tests truncate tables instead of rolling back | The concurrency tests need several real connections committing independently. |
| `/health` always 200, `/ready` 503 | Liveness decides "restart me"; readiness decides "send me traffic". A database blip should not restart a healthy API. |

## HNSW vs a plain index

A B-tree answers "equals / range" questions: it narrows the search with ordering. A vector query asks "which rows are **closest** to this point?", and there is no ordering to exploit in 768 dimensions. Without a special index Postgres computes the distance to every row (an exact but linear scan).

**HNSW** (Hierarchical Navigable Small World) builds a layered graph: each vector links to nearby vectors; upper layers are sparse "highways", the bottom layer holds everyone. A search enters at the top, greedily hops to closer neighbours, and descends. It visits a small fraction of the rows, which makes it fast, but the result is **approximate** (it can miss the true nearest neighbour) and the index is slower to build and larger. `m` (links per node) and `ef_construction` (build effort) trade quality against build cost; `hnsw.ef_search` does the same at query time. The operator class (`vector_cosine_ops`) must match the query operator (`<=>`) or the index is ignored.

## Full-text search vs vector search

| | Full-text (`tsvector` + GIN) | Vector (`embedding` + HNSW) |
| --- | --- | --- |
| Matches on | the **words** (after normalisation) | the **meaning** (embedding proximity) |
| "Where is `get_user_id` defined?" | excellent: exact identifier | may drift to similar-sounding code |
| "How do we authenticate requests?" | misses code that never says "authenticate" | finds the middleware by meaning |
| Result | exact, boolean match + rank | approximate nearest neighbours |
| Cost | cheap, no model | needs an embedding call per chunk and per query |

They fail differently, which is why M9 combines them (reciprocal rank fusion). `tsv` is a **generated column**: Postgres recomputes it from `content` itself, so it cannot go stale.

## Five likely interview questions

1. **What does `SKIP LOCKED` do and why use it for a queue?** `FOR UPDATE` locks the selected rows; `SKIP LOCKED` makes other transactions ignore rows that are currently locked instead of waiting. N workers can each grab a different job without blocking or double-processing, so a plain table works as a safe work queue.
2. **What happens if a worker dies mid-job?** The row stays `running` with an old `locked_at`. `recover_stale_locks` returns jobs older than the visibility timeout to `queued` (or `dead` if attempts are exhausted). The timeout must exceed the slowest legitimate job, otherwise a healthy job is run twice, so handlers should be idempotent.
3. **Why commit in `claim_next` before running the handler?** A long transaction holds a connection and row lock, hurts vacuum and risks timeouts. The `running` status plus `locked_at` is a lease that does not need a transaction.
4. **Why Postgres and not Redis for the queue?** Free-tier constraints and transactional enqueue (delivery + run + job commit together). The cost is throughput and polling latency; the `JobQueue` interface allows swapping later.
5. **Why is `/health` always 200 even when the DB is down?** It is a liveness probe: orchestrators restart on failure, and restarting the API does not fix a database outage. `/ready` returns 503 so traffic can be held back instead.

## Things to try breaking

- Remove `.with_for_update(skip_locked=True)` from `claim_next` and run `test_concurrent_claimers_never_get_the_same_job` and the `SKIP LOCKED` test: see which one fails and how.
- Change `EMBEDDING_DIM` in `constants.py` to `1536` without a migration: the app refuses to start; then set `EMBEDDING_DIM=1536` in the environment alone: still refused.
- Edit a model (add a column) without a migration and run `test_models_and_migrations_have_not_drifted`.
- Stop Postgres (`docker compose stop postgres`) and compare `curl /health` (200, `unavailable`) with `curl /ready` (503).
- Run the seed script with `ENVIRONMENT=production`, then with a non-localhost `DATABASE_URL`.
- In `psql`, `UPDATE jobs SET last_error='x'` and look at `updated_at`: it does not move (raw SQL bypasses `onupdate`); do the same through SQLAlchemy and it does.
- Run `EXPLAIN` on a `ORDER BY embedding <=> ...` query with and without the HNSW index.

## Manual steps for you

1. Review the branch and push it: `git push -u origin feat/m1-database`, then open a PR and wait for CI.
2. If you already had the `codelens_pgdata` volume, nothing is required: the test run creates `codelens_test` itself. For a fully fresh start: `docker compose down -v` (this deletes your dev data), then `docker compose up`.
3. If port 5432 is taken on your Windows machine (a native PostgreSQL service was listening on it here): put `POSTGRES_PORT=54329` (any free port) in the root `.env`, and use the same port in the `DATABASE_URL` line of that file; tests and Alembic read it from there.
4. Apply the schema to your dev database: `cd apps/api && uv run alembic upgrade head`, optionally followed by `ENVIRONMENT=development uv run python -m scripts.seed`.

## Quiz (answer in your own words)

1. Two workers call `claim_next` at the same instant and only one job exists. Walk through what each database transaction does and why exactly one gets the job.
2. A handler takes 10 minutes but the visibility timeout is 5. Describe what goes wrong, what the code does to limit the damage, and what you would change.
3. Why does uninstalling the GitHub App not simply `DELETE` the installation row, given that every foreign key is `ON DELETE CASCADE`?
