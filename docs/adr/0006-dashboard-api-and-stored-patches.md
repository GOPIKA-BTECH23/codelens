# ADR 0006: Dashboard API, stored review patches and their retention (M6)

Status: accepted, 2026-10-17

## Context

M6 adds the web dashboard and the REST endpoints behind it. Three questions needed a decision:

- **Where does the review page get the diff from?** M5 posted the review to GitHub and kept only the findings. The summary text and the diff were never stored.
- **How are the dashboard's endpoints authorized?** Two levels exist (installation role, then `repository_access`, decision 13), and there are now many repo-scoped routes.
- **How do frontend types stay equal to backend models?**

## Decisions

### 1. The worker stores the summary and the reviewed files' patches

Migration `b7e2c4a9d105` adds `review_runs.summary` (JSONB: `what_changed` plus a list of plain-text notes), `findings.dismissed`, `repositories.settings` (JSONB) and a table `review_run_files` (`path`, `old_path`, `status`, `additions`, `deletions`, `patch`). `runs.replace_findings` writes the findings, the summary and the files in **one transaction before posting**, so what the dashboard shows is exactly what was posted, and a retried job replaces rather than duplicates.

Why not fetch the diff from GitHub when the page opens?

| | Store at review time (chosen) | Fetch live |
|---|---|---|
| API process needs the App private key | no | yes (it is worker-only today) |
| Extra GitHub calls per page view | 0 | 1+ (paginated) |
| Shows the commit that was reviewed | always | no: the PR may have moved on, or been force-pushed |
| Works for closed PRs after branch deletion | yes | not reliably |
| Stores source code | **yes** | no |

The last row is the price. Patches are source code, so they fall under the same privacy rules as `code_chunks`.

Only reviewed files get a row (docs, lockfiles and generated files that were filtered out are named in the summary notes). Runs from before M6 have no stored files; the page then shows the findings alone and says why.

### 2. Retention of stored patches

Patches (`review_run_files.patch`) are deleted in two situations:

1. **On uninstall**, immediately, together with `code_chunks`, `chat_sessions` and `chat_messages` (ADR 0001, decision 2). The purge job sets them to `NULL` or deletes the rows; it must never delete the installation.
2. **30 days after the pull request is closed or merged**, even without an uninstall. Nobody reviews a PR that was merged a month ago, and a code copy we no longer need should not outlive its purpose.

The `patch` column is nullable, and the API already treats `NULL` as "not available" (`patch_available: false`), so purged diffs degrade to a message instead of an error. Per-file metadata (`path`, `status`, `additions`, `deletions`) is kept with the review metadata. `findings.message` and `findings.suggestion` keep their separate rule (NULL 30 days after uninstall).

**The purge job itself is not part of M6.** It arrives with the retention work (the `purge_installation` handler is deliberately unregistered until then, ADR 0004). This ADR fixes the rule so that code written later matches it. Until then, patches stay in the database; the README's privacy note (demo repositories only) covers that.

### 3. Authorization is a dependency, and a missing row looks like a missing resource

Each route declares what it needs in its signature, and the check runs before the handler body:

| Dependency | Resolves | Fails with |
|---|---|---|
| `require_repository_role(MEMBER/ADMIN)` | `repository_id` | 404 if not visible (membership AND `repository_access`), 403 if visible but the role is too weak |
| `require_pull_access` | `pull_id` → its repository, then the same check | 404 |
| `require_finding_access` | `finding_id` → run → pull → repository, same check | 404 |

A caller who may not see a resource gets **404, never 403**, so ids cannot be probed for existence. 403 is reserved for "you can see it, but may not do this". `GET /analytics/usage` is scoped to installations where the caller is admin or owner (and to repositories they can see); naming another installation gives 404 or 403 by the same rule.

A parametrized test (`test_every_endpoint_enforces_its_role_boundary`) runs every endpoint as admin, member without access to a repository, an outsider and an anonymous caller.

### 4. Conventions for the list and write endpoints

- Lists return `Page[T]` (`items`, `total`, `limit`, `offset`); `limit` is 1–100 (default 20). Offset pagination is enough at this size and lets the UI show "21–40 of 134".
- `PATCH` endpoints take partial bodies and reject unknown fields (`extra="forbid"`). For `PATCH /findings/{id}`, a missing `user_feedback` means "leave alone" and an explicit `null` clears the vote (`model_fields_set`).
- Feedback values stay `helpful | not_helpful` in the database and API; the UI says "Useful / Not useful".
- Cost fields are floats named `cost_usd` and documented as an **estimate at list price**; the UI labels them "Estimated cost".
- `POST /repos/{id}/index` enqueues an `index_repo` job. Until M8 the worker's handler marks the repository `failed` and the job `dead` with a clear message (a stub that succeeded would hide the missing feature). The UI shows indexing as "not available yet" regardless of status.
- Repository settings (enabled agents, severity threshold, ignore paths) are enforced by the pipeline: a disabled agent skips the run before any GitHub or LLM call, ignore paths join the global ones, and findings under the threshold are dropped and counted in the review notes.

### 5. One source of truth for API types

The Pydantic models define the OpenAPI schema. `scripts/dump_openapi.py` writes it to `apps/web/src/api/openapi.json` (no database needed, placeholder settings so a developer's `.env` cannot change it), and `openapi-typescript` (pinned to 7.13.0, the latest release when checked) turns it into `src/api/schema.ts`. `src/api/types.ts` only gives short names to generated types.

Both generated files are committed, and both are checked: a unit test and a CI step fail if the snapshot differs from the API, and a CI step fails if `schema.ts` differs from a fresh generation. The error shape (`{error: {code, message}}`) is produced by the global handler, not a response model, so it is the one hand-written type.

`openapi-typescript` declares `typescript@^5` as a peer; this project uses TypeScript 6. An npm `overrides` entry accepts it for that one package. The generator only uses the compiler as a library, and the generated file typechecks under TS 6.

## Consequences

- The review page works from our database alone and shows the exact reviewed commit.
- Storing patches widens what a database leak would expose. The mitigation is the retention rule above plus the existing "demo repositories only" stance; the purge job must be built before any private repository is onboarded.
- A new endpoint is secure by default only if it uses one of the guards; the authorization matrix test is where a forgotten guard shows up, so every new route must be added to its table.
