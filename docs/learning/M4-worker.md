# M4: The worker

## What was built

- `app/worker/main.py`: the `Worker`. N claim-and-run loops plus one recovery loop. Run it as its own process (`python -m app.worker.main`) or inside the API (`RUN_WORKER_IN_API=true`).
- `app/worker/handlers.py`: a registry `{job type: handler}` with `review_pr` and `index_repo` **stubs**. `purge_installation` is deliberately missing.
- `app/services/runs.py`: the `review_runs` state machine. `app/services/cost_guard.py`: the daily cost cap hook.
- Queue additions: `mark_dead` (permanent failure) and `release` (give a job back on shutdown).
- Logs carry `job_id` and `run_id` automatically (context variables, like `request_id` in M0).
- A migration allowing the run status `skipped`; a `worker` service in docker-compose.

## The flow in plain words

1. A PR is opened. The webhook (M3) writes a `review_runs` row (`queued`) and a `jobs` row in one transaction and answers 202.
2. A worker loop calls `claim_next()`: one short transaction that locks a queued job with `FOR UPDATE SKIP LOCKED`, sets it `running`, bumps `attempts` and commits. The handler then runs with **no transaction open**.
3. `review_pr` loads the run and decides: already superseded -> exit; already finished -> exit; installation uninstalled -> permanent failure; daily cap reached -> `skipped`; otherwise `queued -> running` (committed, so you can see it), log "NO REVIEW WAS PERFORMED", `running -> succeeded`.
4. The worker marks the job `succeeded`. If the handler raised, the job is retried with backoff, or goes `dead` when attempts run out or the error was permanent.
5. If the worker is told to stop (SIGTERM), it stops claiming, waits up to the grace period for running jobs, then cancels them and **releases** them back to the queue.
6. Every 30 s the recovery loop returns jobs whose lease expired (worker killed) to the queue.

## Why these decisions

- **Why a queue and a separate worker?** Reviews take minutes; GitHub waits 10 s for a webhook answer and does not retry. The queue decouples "accept the event" from "do the slow work", survives restarts, and lets work be retried and rate-limited. A separate worker means a heavy job cannot slow or crash the API, and each can be scaled alone. The in-API mode exists only because the free tier gives us one service.
- **At-least-once delivery.** Exactly-once is not achievable when the process can die between "did the work" and "recorded that it did". We pick "at least once" (a job may run twice, never zero times) and make handlers **idempotent**: every state change is a guarded transition, and a handler that finds its work done exits quietly. M5 must also make GitHub posting safe to repeat (for example by checking whether the review for this commit already exists).
- **Visibility timeout.** `running` + `locked_at` is a lease. If a worker vanishes, nobody releases the row; after the timeout the recovery loop does. Too short, and slow healthy jobs get run twice. Too long, and a crash delays the job. 5 minutes is a guess until M5 measures real review times.
- **Releasing on shutdown refunds the attempt.** A deploy is not the job's fault, so it must not eat one of its five retries.
- **Why `purge_installation` is unregistered.** A fake "done" on a data-deletion duty is worse than a visible failure (ADR 0004).
- **Why the stub writes `model = 'm4-stub-no-review'`.** A `succeeded` run with no findings looks like "the code is clean". The data must not say that.
- **State changes are `UPDATE ... WHERE status IN (...)`.** If a push supersedes a run while the worker is busy, the worker's late `mark_succeeded` matches no row and does nothing; the newer decision wins.

## Likely interview questions

1. **Why Postgres as the queue instead of Redis/SQS?** The job row is written in the same transaction as the webhook delivery and run row, so we cannot lose a job or enqueue a phantom one. `SKIP LOCKED` gives safe concurrent claiming. It needs no extra service on a free tier. It is behind a `JobQueue` interface, and the trade-off is polling load and lower throughput than Redis.
2. **How do you stop two workers processing the same job?** `SELECT ... FOR UPDATE SKIP LOCKED` in the claim transaction: a locked row is skipped, not waited on. Completion is guarded by `WHERE id=... AND status='running' AND attempts=<mine>`, so a worker whose lease expired cannot overwrite the new owner's result.
3. **What happens if the worker is killed mid-job?** The row stays `running`. After the visibility timeout `recover_stale_locks` re-queues it (or marks it `dead` if attempts are used up). The handler runs again, which is why it must be idempotent.
4. **Retry or not?** Transient errors (network, 5xx) retry with exponential backoff up to `max_attempts`; permanent ones (run deleted, repo uninstalled) are `dead` immediately because retrying cannot help. Unknown exceptions are treated as transient.
5. **Tradeoffs of running the worker inside the API?** Shared memory (512 MB) and event loop, no failure isolation, stops whenever the API sleeps or deploys, cannot scale independently. Fine for a demo, not for production.

## Things to try breaking

- Start two `python -m app.worker.main` processes with 50 queued jobs and check `attempts = 1` for all of them.
- `docker compose kill -s SIGKILL worker` during a job, then watch the job come back after `JOB_VISIBILITY_TIMEOUT_SECONDS` (set it to 30 to see it quickly).
- `docker compose stop worker` mid-job: the log says "job released back to the queue" and `attempts` goes back down.
- Set `DAILY_COST_CAP_USD=0` and open a PR: the run becomes `skipped` with the reason in `error`.
- Push twice quickly to one PR: the older run stays `superseded` and its job exits early.
- Uninstall the App (or `UPDATE installations SET uninstalled_at = now()`) and queue a review: the job is `dead` after one attempt, the run `failed`.
- Look at `SELECT type, last_error FROM jobs WHERE status='dead'` after an uninstall: `purge_installation` is there, on purpose.
- Delete the `HANDLERS` entry for `index_repo` and push to `main`: that job type becomes `dead` too.

## Quiz (answer in your own words)

1. Two workers call `claim_next()` at the same instant and there is exactly one queued job. Walk through what Postgres does so that only one gets it, and say what the other one returns.
2. A handler finishes its work, then the worker process is killed before it can call `mark_succeeded`. What happens next, and what property must the handler have for that to be safe?
3. Why does the `purge_installation` job end up `dead` instead of `succeeded` right now, and what would be wrong with registering an empty handler "for now"?
