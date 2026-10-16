# ADR 0003: Webhook intake, GitHub App auth and the REST client's retry policy (M3)

Status: accepted, 2026-10-16

## Context

M3 connects CodeLens to GitHub as a **GitHub App**: GitHub calls us (webhooks), and the worker calls GitHub as the App (installation tokens). Facts this ADR relies on, checked in GitHub's docs on 2026-10-16:

- Webhooks are signed with HMAC-SHA256 over the body: `X-Hub-Signature-256: sha256=<hex>`.
- GitHub waits **10 seconds** for a 2xx, and it **does not redeliver failed deliveries automatically**. A manual redelivery keeps the same `X-GitHub-Delivery`.
- `installation` and `installation_repositories` go to every App automatically. `pull_request` (needs Pull requests: read) and `push` (needs Contents: read) are opt-in.
- The app JWT is RS256, `iat` 60 s in the past, `exp` at most 10 minutes ahead, and `iss` is the client ID (recommended) or the app ID. Installation tokens live one hour, and since 2026-04-27 they may use a longer, stateless format.
- Rate limits answer **403 or 429**. The documented wait order is: `retry-after`, then `x-ratelimit-reset` when remaining is 0, then at least 60 s with exponential growth.

## Decisions

### 1. The endpoint does only cheap, local work, in one transaction

`POST /webhooks/github` steps:

1. Read the raw body, capped at 25 MB (GitHub's own payload cap).
2. Verify the signature.
3. Record the delivery.
4. Write the rows and enqueue a job, in the **same database transaction**.
5. Commit, then answer.

It never calls GitHub or an LLM.

- New deliveries answer **202**, including ones we deliberately ignore, such as drafts or `ping`.
- Duplicates answer **200**.
- A bad or missing signature answers **401**, missing headers or bad JSON **400**, and an oversized body **413**.

One transaction means a delivery is never marked "seen" without its job, or the reverse. If a handler fails, everything rolls back, the delivery stays unrecorded, and a redelivery from the GitHub UI works.

### 2. Idempotency comes from the database, not from application checks

`webhook_deliveries.delivery_id` is UNIQUE. We insert with `ON CONFLICT DO NOTHING RETURNING id`, and no returned row means a duplicate. Two concurrent copies of one delivery are safe: the second insert waits on the unique index until the first commits, then conflicts (`tests/integration/test_webhook_concurrency.py`).

### 3. Superseding is serialised by row locks

A review-triggering `pull_request` event upserts the PR (`ON CONFLICT DO UPDATE`, which locks the row), marks the PR's `queued`/`running` runs `superseded`, inserts a new `queued` run, and enqueues `review_pr {review_run_id, head_sha}`. Because the upserts lock the installation, repository and PR rows until commit, two deliveries for one installation run one after the other, and the later one sees and supersedes the earlier one's run.

**Known limit:** the run created *last* wins, not the one for the *newest commit*. A late, out-of-order `synchronize` for an older commit could supersede a newer run. Two things limit the damage:

- the worker re-checks run status right before posting (decision 16);
- M5 can compare `head_sha` with the PR's live head.

### 4. Payload gaps are filled from later payloads, not by API calls

Installation payloads list repos without `default_branch`. A new repo row gets the column default, and an existing row keeps its value. The next `pull_request` or `push` payload corrects it, since both carry the full repository. The push filter compares `ref` with the **payload's** `default_branch`, so a placeholder can never misroute indexing. Calling the API from the webhook to fill the gap would break decision 1.

### 5. The JWT issuer is the client ID

GitHub recommends the client ID. We already require it (`GITHUB_OAUTH_CLIENT_ID`, decision 11), so `GITHUB_APP_ID` is dropped.

`GITHUB_APP_PRIVATE_KEY` is optional in M3 (only the worker uses it) but is parsed at startup when set. It accepts:
- a file path (simplest outside Docker);
- base64 PEM (simplest for compose and dashboards);
- PEM text.

### 6. Installation tokens are cached in memory

Tokens are cached per installation until **5 minutes before expiry**, so a job never starts with a nearly expired token. A per-installation `asyncio.Lock` means concurrent jobs trigger one token request, not N. A 401 from the API invalidates the cached token and retries once with a fresh one. Losing the cache on restart costs one HTTP call per installation, so no shared cache (Redis, database) is needed.

### 7. Retry policy: never retry a write that may have happened

| Request | 5xx / network error | Rate limited (403/429) |
| --- | --- | --- |
| GET (read) | retry with backoff `1 s, 2 s, 4 s` + jitter, 3 retries | wait as GitHub says, retry |
| POST that creates (review, comment) | **no retry**, raise `GitHubServerError` | wait as GitHub says, retry |

A 502 or a timeout after a POST does not tell us whether GitHub created the review. Retrying could post the same review twice on someone's PR, which is worse than a failed job. A rate-limit answer, by contrast, means GitHub rejected the request without doing it, so retrying is safe.

Waits longer than 60 s are not slept. The client raises `GitHubRateLimited(retry_at)`, so the worker can put the job back in the queue with `run_after = retry_at` (M4) instead of tying up a worker for half an hour. Mutating requests are spaced at least 1 s apart, as GitHub's best-practice guide asks.

### 8. Local tunnel: ngrok, not smee

smee-client receives the event, parses it and re-serialises the JSON before forwarding it. Different bytes mean a different HMAC, so valid deliveries can fail with 401, for example when a PR title contains non-ASCII characters. ngrok forwards the raw HTTP request unchanged. The free plan includes one dev domain tied to the account; check current limits on ngrok's pricing page.

## Consequences

- The endpoint answers in milliseconds: 9–38 ms in a local replay, plus a one-off ~450 ms while the connection pool connected. That is far inside GitHub's 10-second limit, even on a slow free-tier host once it is awake. Render cold starts remain decision 2's problem.
- `index_repo` and `purge_installation` jobs are enqueued before their handlers exist (M8 and later). Until then the M4 worker must treat unknown job types as permanent failures (`dead`), not retry them forever.
- `suspend`/`unsuspend` events are recorded but not acted on: there is no column for suspension yet.
