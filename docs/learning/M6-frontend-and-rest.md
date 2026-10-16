# M6: Dashboard UI and the REST endpoints behind it

## What was built

**Backend** (all under `apps/api/app/`)

- `api/repos.py`, `pulls.py`, `findings.py`, `analytics.py`: the endpoints. Routes only declare dependencies and call a service.
- `services/repos.py`, `reviews.py`, `analytics.py`: the queries and rules.
- `schemas/dashboard.py`: every request and response model (these become the OpenAPI schema).
- `core/pagination.py`: `?limit=&offset=` in, `Page[T]` out, for every list.
- `deps.py` + `services/rbac.py`: `require_repository_role`, `require_pull_access`, `require_finding_access`.
- Migration `b7e2c4a9d105`: `findings.dismissed`, `repositories.settings`, `review_runs.summary`, table `review_run_files`.
- The M5 pipeline now **stores** the summary and the reviewed patches, and **obeys** the repo settings (agents, severity threshold, ignore paths).
- `index_repo` stub now fails visibly instead of pretending.

**Frontend** (`apps/web/src/`)

- `api/openapi.json` → `api/schema.ts` (generated) → `api/types.ts` (short names) → `api/client.ts` (fetch + refresh) → `api/queries.ts` (TanStack Query hooks).
- Pages: landing, login, dashboard, repo (PR list + admin settings), PR review (summary, diff with inline findings, filters, feedback), analytics (admin).
- Shell: nav, theme toggle (class-based dark mode), toasts in a live region, skip link.
- `lib/suggestion.ts`: splits a model's suggestion into prose (plain text) and code (code block).

## The request lifecycle: a click on "Useful"

1. **Click.** `FindingCard` calls `onUpdate({ user_feedback: 'helpful' })`. The button is a toggle (`aria-pressed`): clicking the active one sends `null` to clear it.
2. **Optimistic write.** `useUpdateFinding.onMutate` cancels in-flight refetches of this review, remembers the finding's old `user_feedback`/`dismissed`, and writes the new value straight into the query cache. React re-renders: the button is pressed *before* any network traffic.
3. **HTTP.** `api.updateFinding` → `PATCH /api/findings/{id}` with JSON and the `X-Requested-With` header (CSRF layer 2). In dev, Vite's proxy strips `/api`; in production a Vercel rewrite does. Cookies (httpOnly, same origin) ride along. A 401 triggers one single-flight refresh and a replay.
4. **Middleware.** Request ID → security headers → CORS → CSRF (header present?) → router.
5. **Dependencies run before the handler.** `get_current_user` decodes the JWT cookie and loads the user. `require_finding_access` joins finding → run → pull request → repository and checks membership *and* `repository_access`. No row → `NotFoundError` → the global handler turns it into `404 {"error": {...}}`.
6. **Handler → service.** `patch_finding` calls `reviews.update_finding`, which sets only the fields that were sent (`model_fields_set` tells "absent" from "null") and flushes.
7. **Commit.** The session dependency commits when the route function returns (`scope="function"`), so the row is durable before the response goes out.
8. **Response.** `FindingOut` JSON goes back; the client resolves the mutation.
9. **Settle.** On success or failure `onSettled` invalidates `['review', pullId]` and `['pulls']`. The review refetches, so the cache ends up equal to the database, and the PR list's "open findings" count updates.
10. **On failure** (`onError`): only this finding's old values are put back, a toast says what happened, and the settle refetch confirms the truth.

## How TanStack Query caching and optimistic updates work

- **Cache by key.** `['review', pullId]` identifies one piece of server state. Two components asking for it share one request and one cache entry.
- **Stale-while-revalidate.** Data is shown from cache immediately and refetched in the background when it is stale (on mount, on window focus). `placeholderData: keepPreviousData` keeps the old page visible while the next page of a list loads.
- **Invalidation** marks keys stale and refetches the ones on screen. Hierarchical keys let `['pulls']` cover every page and filter of every repo.
- **Optimistic update recipe:** `cancelQueries` (so a late refetch cannot overwrite your guess) → snapshot → `setQueryData` → on error restore → always invalidate. We restore *one finding's fields*, not the whole snapshot: if you click Useful on finding A (slow) and then Useful on finding B (fast), rolling back A with a whole-page snapshot would also erase B. A test covers exactly this.
- **Polling:** `useReview` sets `refetchInterval` only while the run is `queued`/`running`, so a review that is in progress completes on screen by itself.

## REST design choices

- **Nouns and nesting only where the parent scopes the child:** `/repos/{id}/pulls` (the list is scoped by the repo), but `/pulls/{id}` and `/findings/{id}` are flat because the id is globally unique and the guard walks up to the repo.
- **`PATCH` with partial bodies**, not `PUT`: feedback and dismissal are independent, and a UI should not have to resend the whole finding.
- **Pagination everywhere** with a `total`. Offset pagination is simple and fine at this size; cursor pagination would matter for feeds that change while you scroll.
- **404 instead of 403 for things you cannot see.** 403 would confirm that an id exists. 403 is only used when you can see the repo but your role is too weak (e.g. a member calling `POST /repos/{id}/index`).
- **`GET /pulls/{id}/review` returns `null`, not 404,** for a PR that was never reviewed: "no review yet" is a valid answer, not an error.
- **Money is labelled.** `cost_usd` is an estimate at list price; the UI says "Estimated cost" everywhere.
- **OpenAPI is the contract.** Descriptions, examples, error responses and a `Page[T]` envelope are in the schema; the TypeScript types are generated from it.

## Why authorization checks live in dependencies

- **They cannot be forgotten halfway.** A dependency in the route signature runs before the body; there is no code path in the handler where "the check" can be skipped by an early return or a refactor.
- **They are declarative and greppable.** `AdminDep` in a signature *is* the policy; a reviewer sees it in one line. The matrix test lists every route against every kind of caller, so a route added without a guard fails review.
- **One implementation of "who can see what".** Routes, services and tests share `rbac.get_repository_access`; the join (membership AND `repository_access`) exists once.
- **Handlers stay about the feature.** They receive an already-authorized `Repository`/`Finding` object, not an id they must remember to check.
- **The frontend's checks are not security.** Hiding the settings form from members and the Analytics link from non-admins is convenience; the API answers 403/404 regardless.

## Other decisions worth knowing

- **Why store patches** instead of calling GitHub: ADR 0006 (needs no App key in the API process, shows the exact reviewed commit, costs storing source code, hence the retention rule).
- **Suggestions are classified, not trusted.** Fenced blocks are code; unfenced lines count as code only if they clearly look like it (assignment, call, block syntax, `def`/`import`/…); sentences, including ones that mention `return` or `if`, stay plain text. All of it is rendered as React text, so a model cannot inject HTML (a test feeds `<img onerror>`).
- **Charts:** one measure per chart, so no legend; thin rounded bars on a baseline; every bar focusable with a tooltip; "Show as table" so the data never depends on seeing colour.
- **Dark mode** is a class on `<html>` (Tailwind `dark` variant on a class), set by an inline script before first paint to avoid a white flash.

## 5 likely interview questions

1. **What happens between clicking a button and the database changing?** *(The lifecycle above: optimistic cache write, PATCH with cookie + CSRF header, dependencies authenticate and authorize, service updates, commit, JSON back, invalidate and refetch.)*
2. **How do optimistic updates work, and what can go wrong?** *(Write to cache first; snapshot; roll back on error; invalidate on settle. Pitfalls: a late refetch overwriting the guess → `cancelQueries`; rolling back a whole snapshot undoing a concurrent success → roll back per item.)*
3. **Why return 404 instead of 403 for other people's resources?** *(Do not confirm existence; ids cannot be enumerated. 403 only when the caller already knows it exists.)*
4. **How do you keep frontend and backend types in sync?** *(Pydantic → OpenAPI snapshot → `openapi-typescript`; both files committed; a test and CI fail when stale; the one hand-written type is the error envelope.)*
5. **Model output is untrusted. How does the UI stay safe?** *(Rendered as text, never HTML; suggestions classified conservatively; findings come only from validated, diff-anchored output; the API never lets model text decide anything.)*

## Things to try breaking

- Remove `Depends(require_finding_access)` from `patch_finding`; run `tests/integration/test_dashboard_api.py` and see which cases fail.
- Make the PATCH return 500 (e.g. stop the API) and click Useful: the button flips, then flips back with a toast.
- Click Useful on one finding, and quickly on another while the first is slow; then fail the first. Replace per-finding rollback with `setQueryData(key, snapshot)` and watch the second one vanish.
- Edit `schemas/dashboard.py` (add a field) without regenerating: `uv run pytest tests/unit/test_openapi_snapshot.py` fails; run the two commands from CLAUDE.md and watch `schema.ts` change.
- Put a fake `<script>` or `<img onerror>` in a finding's message in the database; confirm it shows as text.
- Set a repo's severity threshold to `critical` and push to a PR: the review notes say how many findings were hidden.
- Open the review page of a run from before M6: no diff is stored, the page says so and still lists the findings.

## Manual steps for you

1. **Apply the migration:** `cd apps/api && uv run alembic upgrade head` (docker compose does not run migrations).
2. **Rebuild the web container** (`package.json` changed): `docker compose up --build -V`.
3. **Produce a review that has a diff:** open or push to a PR on a repo where the dev App is installed (reviews from before M6 have findings but no stored diff).
4. **Click through:** `http://localhost:5173` → Sign in with GitHub → Dashboard → the repo → the PR → press Useful on a finding → reload and check it stuck.
5. **Check the outsider case:** sign in with a *second* GitHub account that does not belong to the installation (a private window works). Its dashboard must be empty; paste the first account's `/repos/<id>` or `/pulls/<id>` URL: it must show "not found".
6. **Admin pages:** with the admin account, change a repo's severity threshold, then open a new PR and read the review notes; open `/analytics` after a few reviews.
7. **Optional:** run `docker run --rm -v "$(pwd):/repo" -w /repo rhysd/actionlint:latest` to lint the changed CI workflow.
8. **Push the branch yourself:** `git push -u origin feat/m6-dashboard`, then open the PR.

## Quiz (answer in your own words)

1. A member of an installation (not an admin) sends `PATCH /repos/{id}/settings` for a repo they can see, and later the same request for a repo they cannot see. Which status codes come back, and why different ones?
2. In `useUpdateFinding`, why does `onMutate` call `cancelQueries` first, and why does `onError` restore one finding's fields instead of the whole cached review?
3. Why does the review page need patches stored in our database, what does that cost us, and what rule limits the cost?
