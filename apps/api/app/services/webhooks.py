"""Webhook intake: record the delivery, update our rows, enqueue jobs. Nothing slow.

Golden rule (CLAUDE.md section 4): no LLM calls, no GitHub API calls, no diffs here. Every
handler is a few small SQL statements, so the endpoint answers in milliseconds and GitHub
(which gives up after 10 seconds and does not retry on its own) always sees a 2xx.

Everything for one delivery happens in the request's ONE transaction: the delivery row,
the PR/run rows and the job commit together or not at all. If anything fails, the delivery
is not marked as seen, so a redelivery from the GitHub UI is processed normally.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.db.models import (
    Installation,
    PullRequest,
    Repository,
    RepositoryAccess,
    ReviewRun,
    WebhookDelivery,
)
from app.db.models.enums import IndexStatus, PullRequestState, RunStatus
from app.services import github_events as gh
from app.services.github_sync import ACCOUNT_TYPES, upsert_installation, upsert_repository
from app.services.jobs import JobQueue

JOB_REVIEW_PR = "review_pr"
JOB_INDEX_REPO = "index_repo"  # handler arrives in M8
JOB_PURGE_INSTALLATION = "purge_installation"  # deletes code_chunks + chat (ADR 0001)

# pull_request actions that mean "the code to review changed (or became reviewable)".
REVIEW_ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})
# Actions that only change what we display about the PR.
PR_UPDATE_ACTIONS = frozenset({"closed", "edited"})
INSTALL_ACTIONS = frozenset({"created", "new_permissions_accepted"})

_MAX_ACTION_LENGTH = 50  # webhook_deliveries.action column
_TITLE_LENGTH = 500  # pull_requests.title column

logger = logging.getLogger("app.webhooks")


@dataclass(frozen=True)
class WebhookPolicy:
    """Configurable filters (from Settings)."""

    ignored_pr_authors: frozenset[str]


@dataclass(frozen=True)
class WebhookResult:
    """What happened to a delivery. `duplicate` means we had already processed it."""

    status: Literal["duplicate", "queued", "recorded", "ignored"]
    reason: str | None = None


def _ignored(reason: str) -> WebhookResult:
    return WebhookResult("ignored", reason)


Handler = Callable[
    [AsyncSession, JobQueue, WebhookPolicy, Mapping[str, Any]], Awaitable[WebhookResult]
]


async def process_delivery(
    session: AsyncSession,
    queue: JobQueue,
    policy: WebhookPolicy,
    *,
    delivery_id: str,
    event: str,
    payload: Mapping[str, Any],
) -> WebhookResult:
    """Record one verified delivery and act on it. Only flushes; the caller commits.

    Raises BadRequestError if a handled event's payload does not have the expected shape
    (the transaction then rolls back, so the delivery can be redelivered after a fix).
    """
    raw_action = payload.get("action")
    action = raw_action if isinstance(raw_action, str) else None
    if not await record_delivery(session, delivery_id, event, action):
        result = WebhookResult("duplicate")
    elif (handler := _HANDLERS.get(event)) is None:
        result = _ignored(f"event '{event}' is not handled")
    else:
        try:
            result = await handler(session, queue, policy, payload)
        except ValidationError as exc:
            # Field locations only: the payload itself may contain private repo details.
            fields = sorted({".".join(map(str, err["loc"])) for err in exc.errors()})
            logger.warning(
                "webhook payload rejected",
                extra={"delivery_id": delivery_id, "event": event, "fields": fields[:10]},
            )
            raise BadRequestError(f"Unexpected '{event}' payload shape") from exc
    logger.info(
        "webhook processed",
        extra={
            "delivery_id": delivery_id,
            "event": event,
            "action": action,
            "status": result.status,
            "reason": result.reason,
        },
    )
    return result


async def record_delivery(
    session: AsyncSession, delivery_id: str, event: str, action: str | None
) -> bool:
    """Insert the delivery; False if this X-GitHub-Delivery was already recorded.

    The UNIQUE constraint does the work: ON CONFLICT DO NOTHING returns no row for a
    duplicate. Two concurrent copies of one delivery cannot both win either: the second
    insert waits on the unique index until the first transaction commits, then conflicts.
    """
    stmt = (
        insert(WebhookDelivery)
        .values(
            delivery_id=delivery_id,
            event=event,
            action=action[:_MAX_ACTION_LENGTH] if action else None,
        )
        .on_conflict_do_nothing(index_elements=[WebhookDelivery.delivery_id])
        .returning(WebhookDelivery.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


# --- pull_request ---------------------------------------------------------------------------


async def _on_pull_request(
    session: AsyncSession, queue: JobQueue, policy: WebhookPolicy, payload: Mapping[str, Any]
) -> WebhookResult:
    action = payload.get("action")
    if action not in REVIEW_ACTIONS and action not in PR_UPDATE_ACTIONS:
        return _ignored(f"pull_request action '{action}' is not handled")
    event = gh.PullRequestEvent.model_validate(payload)
    repository_id = await _ensure_repository(session, event.installation.id, event.repository)
    if repository_id is None:
        return _ignored("unsupported account type")

    pr = event.pull_request
    pull_request_id = await _upsert_pull_request(session, repository_id, pr)
    if action in PR_UPDATE_ACTIONS:
        return WebhookResult("recorded")
    if pr.draft:
        return _ignored("draft pull request")
    if pr.user.login in policy.ignored_pr_authors:
        return _ignored("pull request author is ignored")

    superseded = await _supersede_active_runs(session, pull_request_id)
    run = ReviewRun(pull_request_id=pull_request_id)
    session.add(run)
    await session.flush()
    await queue.enqueue(
        session, JOB_REVIEW_PR, {"review_run_id": str(run.id), "head_sha": pr.head.sha}
    )
    logger.info(
        "review run queued",
        extra={"run_id": str(run.id), "superseded_runs": superseded, "pr_number": pr.number},
    )
    return WebhookResult("queued")


async def _upsert_pull_request(
    session: AsyncSession, repository_id: uuid.UUID, pr: gh.PullRequest
) -> uuid.UUID:
    """Create or refresh the PR row.

    Besides keeping the row current, this matters for concurrency: ON CONFLICT DO UPDATE
    locks the PR row until our transaction commits. A second webhook for the same PR (say
    `synchronize` right after `opened`) therefore waits, and only then supersedes, so it
    sees the first one's committed run and the two cannot both stay queued. (The
    installation and repository upserts before this lock their rows the same way, so in
    practice deliveries for one installation run one at a time. Each transaction takes
    milliseconds, so that costs nothing; tests/integration/test_webhook_concurrency.py.)
    """
    if pr.merged:
        state = PullRequestState.MERGED
    elif pr.state == "closed":
        state = PullRequestState.CLOSED
    else:
        state = PullRequestState.OPEN
    values = {
        "title": pr.title[:_TITLE_LENGTH],
        "author_login": pr.user.login,
        "state": state.value,
    }
    stmt = (
        insert(PullRequest)
        .values(repository_id=repository_id, number=pr.number, **values)
        .on_conflict_do_update(
            constraint="uq_pull_requests_repo_number",
            set_={**values, "updated_at": func.now()},
        )
        .returning(PullRequest.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def _supersede_active_runs(session: AsyncSession, pull_request_id: uuid.UUID) -> int:
    """Mark the PR's queued/running runs superseded (a newer push makes them stale).

    A running run is not interrupted here; its worker checks the status before posting
    (decision 16) and drops the result. Their jobs stay in the queue and exit early (M4).
    """
    result = await session.execute(
        update(ReviewRun)
        .where(
            ReviewRun.pull_request_id == pull_request_id,
            ReviewRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
        )
        .values(status=RunStatus.SUPERSEDED, finished_at=func.now())
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


# --- push -------------------------------------------------------------------------------------


async def _on_push(
    session: AsyncSession, queue: JobQueue, policy: WebhookPolicy, payload: Mapping[str, Any]
) -> WebhookResult:
    event = gh.PushEvent.model_validate(payload)
    if event.deleted:
        return _ignored("branch deleted")
    # The payload's default_branch is authoritative (our row may still hold a placeholder).
    if event.ref != f"refs/heads/{event.repository.default_branch}":
        return _ignored("not the default branch")
    repository_id = await _ensure_repository(session, event.installation.id, event.repository)
    if repository_id is None:
        return _ignored("unsupported account type")
    await session.execute(
        update(Repository)
        .where(Repository.id == repository_id)
        .values(index_status=IndexStatus.QUEUED)
    )
    await queue.enqueue(
        session, JOB_INDEX_REPO, {"repository_id": str(repository_id), "commit_sha": event.after}
    )
    return WebhookResult("queued")


# --- installation / installation_repositories ------------------------------------------------


async def _on_installation(
    session: AsyncSession, queue: JobQueue, policy: WebhookPolicy, payload: Mapping[str, Any]
) -> WebhookResult:
    action = payload.get("action")
    if action not in INSTALL_ACTIONS and action != "deleted":
        # suspend / unsuspend: recorded, but we keep no suspension state yet.
        return _ignored(f"installation action '{action}' is not handled")
    event = gh.InstallationEvent.model_validate(payload)
    installation_id = await _upsert_installation_info(session, event.installation)
    if installation_id is None:
        return _ignored("unsupported account type")

    if action == "deleted":
        # ADR 0001: mark, never delete rows here. RBAC already hides uninstalled
        # installations; the purge job removes code chunks and chat data.
        await session.execute(
            update(Installation)
            .where(Installation.id == installation_id, Installation.uninstalled_at.is_(None))
            .values(uninstalled_at=func.now())
        )
        await queue.enqueue(
            session, JOB_PURGE_INSTALLATION, {"installation_id": str(installation_id)}
        )
        return WebhookResult("queued")

    for repo in event.repositories:
        await _upsert_repository_summary(session, installation_id, repo)
    return WebhookResult("recorded")


async def _on_installation_repositories(
    session: AsyncSession, queue: JobQueue, policy: WebhookPolicy, payload: Mapping[str, Any]
) -> WebhookResult:
    action = payload.get("action")
    if action not in ("added", "removed"):
        return _ignored(f"installation_repositories action '{action}' is not handled")
    event = gh.InstallationRepositoriesEvent.model_validate(payload)
    installation_id = await _upsert_installation_info(session, event.installation)
    if installation_id is None:
        return _ignored("unsupported account type")

    for repo in event.repositories_added:
        await _upsert_repository_summary(session, installation_id, repo)
    removed_ids = [repo.id for repo in event.repositories_removed]
    if removed_ids:
        # Users lose sight of the repo at once (decision 14); the repository and its review
        # history are kept (ADR 0001), exactly like an uninstall.
        await session.execute(
            delete(RepositoryAccess).where(
                RepositoryAccess.repository_id.in_(
                    select(Repository.id).where(
                        Repository.installation_id == installation_id,
                        Repository.github_repo_id.in_(removed_ids),
                    )
                )
            )
        )
    return WebhookResult("recorded")


# --- shared helpers ---------------------------------------------------------------------------


async def _ensure_repository(
    session: AsyncSession, github_installation_id: int, repo: gh.Repository
) -> uuid.UUID | None:
    """Upsert the installation and repository a pull_request/push payload refers to.

    So a missed `installation` webhook (for example, the App was installed before our
    server was reachable) does not break intake. The repo owner is the installation's
    account. None for account types we do not support.
    """
    account_type = ACCOUNT_TYPES.get(repo.owner.type)
    if account_type is None:
        return None
    installation_id = await upsert_installation(
        session,
        github_installation_id=github_installation_id,
        account_login=repo.owner.login,
        account_type=account_type,
    )
    return await upsert_repository(
        session,
        installation_id=installation_id,
        github_repo_id=repo.id,
        full_name=repo.full_name,
        is_private=repo.private,
        default_branch=repo.default_branch,
    )


async def _upsert_installation_info(
    session: AsyncSession, info: gh.InstallationInfo
) -> uuid.UUID | None:
    account_type = ACCOUNT_TYPES.get(info.account.type or "")
    if account_type is None or not info.account.login:
        return None  # enterprise-level installation: out of scope
    return await upsert_installation(
        session,
        github_installation_id=info.id,
        account_login=info.account.login,
        account_type=account_type,
    )


async def _upsert_repository_summary(
    session: AsyncSession, installation_id: uuid.UUID, repo: gh.RepositorySummary
) -> None:
    await upsert_repository(
        session,
        installation_id=installation_id,
        github_repo_id=repo.id,
        full_name=repo.full_name,
        is_private=repo.private,
        default_branch=None,  # not in installation payloads; see github_sync.upsert_repository
    )


_HANDLERS: dict[str, Handler] = {
    "pull_request": _on_pull_request,
    "push": _on_push,
    "installation": _on_installation,
    "installation_repositories": _on_installation_repositories,
}
