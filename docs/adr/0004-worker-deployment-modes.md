# ADR 0004: Worker design, deployment modes and unhandled job types (M4)

Status: accepted, 2026-10-17

## Context

The webhook only records work (ADR 0003). M4 adds the process that does it. Constraints from CLAUDE.md: Postgres is the queue (no Redis), the free Render tier has no free worker service (decision 3), and the API instance has 512 MB of RAM (decision 5).

## Decisions

### 1. One `Worker` class, two ways to host it

`Worker.run(stop_event)` is the whole worker. It is hosted either as:

| | Separate process (`python -m app.worker.main`) | In the API process (`RUN_WORKER_IN_API=true`) |
|---|---|---|
| Used for | local dev, compose `worker` service, any paid host | the free-tier demo (one Render service) |
| Failure isolation | a crashing or memory-hungry job cannot take the API down | a job that blows the 512 MB limit kills the API too |
| Event loop | its own | shared: a CPU-heavy step in a handler (diff parsing, tree-sitter) delays web requests |
| Deploys / sleep | independent | the worker stops whenever the API restarts or the free instance sleeps; queued jobs wait for the next wake-up |
| Scaling | run more workers; `SKIP LOCKED` keeps them from colliding | one process only |

The local default is the separate process; production on the free tier uses the in-API mode (decision 3) with the pinger from decision 2. Both modes use the same shutdown path, so the in-API worker drains on the API's own SIGTERM.

### 2. Delivery is at-least-once; handlers must be idempotent

A claimed job is leased (`status=running`, `locked_at`). If the worker dies, the lease expires after `JOB_VISIBILITY_TIMEOUT_SECONDS` and the job runs again, so a handler may run twice. Every `review_runs` change is a guarded transition (`UPDATE ... WHERE status IN (allowed sources)`), and a handler that finds its work already done returns without changing anything.

### 3. Failure classes

- **Transient** (any exception): retry with exponential backoff (queue: 30 s, 60 s, ... up to 1 h), `dead` after `max_attempts`. The run goes back to `queued` with the error visible, and to `failed` on the last attempt.
- **Permanent** (`PermanentJobError`: run missing, installation uninstalled, malformed payload): the job is `dead` at once, no retries.
- **Shutdown**: not a failure. The job is **released** (`queued`, runnable now, the attempt refunded), so deploys cannot burn a job's retries.

### 4. `purge_installation` has no handler on purpose

The webhook already enqueues `purge_installation` when the App is uninstalled. Deleting an uninstalled customer's code chunks and chat history is a **data-deletion obligation** (ADR 0001), and its handler is not written yet. A no-op stub that returns success would mark the job `succeeded` and make it look as if the data had been purged when it had not.

So the job type is deliberately **not registered**. The worker marks any job without a handler `dead` with `no handler registered for job type 'purge_installation'`, without retries. A dead job is visible (`SELECT type, last_error FROM jobs WHERE status = 'dead'`) and stays that way until a human acts. Once the handler exists, those dead jobs can be re-queued (`status='queued', attempts=0`) and will then really delete the data. A test pins that the type stays unregistered until then.

By contrast the `index_repo` stub **does** succeed (it logs "not implemented"): nothing is owed to anyone if indexing is late, and failing every push would flood the dead-job list.

### 5. The `review_pr` stub must not pass for a review

Until M5 the handler moves the run `queued -> running -> succeeded` and performs no review. To keep that from being mistaken for a real one, it logs a WARNING "NO REVIEW WAS PERFORMED" and writes `review_runs.model = 'm4-stub-no-review'`, so the data says it too. M5 replaces both.

### 6. Daily cost cap

The guard sums `review_runs.cost_usd` for runs that started since 00:00 UTC (database clock) and compares it with `DAILY_COST_CAP_USD`. When the cap is reached the run is `skipped` with the reason in `review_runs.error`, and the job is considered done (retrying cannot help before tomorrow). The check is made before a run starts; runs already in flight can overshoot the cap by their own cost. A cap of 0 blocks everything. Until M5 records costs the total is 0.

## Known gaps (not fixed in M4)

- If a worker crashes repeatedly and the job runs out of attempts through stale-lock recovery alone, the job goes `dead` but its run stays `running`. A sweep for orphaned runs is a later hardening step.
- Shutdown via SIGTERM is covered by tests through the stop event; the real signal path was run on Windows only (`signal.signal` fallback) and is expected to work with `add_signal_handler` in the Linux container, but is unverified there.
- The worker needs a restart to pick up code changes (compose does not run it with `--reload`).
