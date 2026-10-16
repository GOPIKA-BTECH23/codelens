"""POST /webhooks/github end to end: signature, idempotency, and the rows/jobs each event
creates, against the real test database. GitHub calls the API directly (no /api proxy,
no cookies, no CSRF header), and so do these tests.
"""

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import webhooks as webhooks_api
from app.config import Settings
from app.core.webhook_signature import compute_signature
from app.db.models import (
    Installation,
    Job,
    PullRequest,
    Repository,
    RepositoryAccess,
    ReviewRun,
    User,
    WebhookDelivery,
)
from app.main import create_app
from tests.app_utils import TEST_WEBHOOK_SECRET, make_settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "webhooks"
URL = "/webhooks/github"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def deliver(
    client: TestClient,
    event: str,
    payload: dict[str, Any] | bytes,
    *,
    delivery_id: str | None = None,
    secret: str = TEST_WEBHOOK_SECRET,
    signature: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Send a delivery the way GitHub does: compact JSON body, signed, with its headers."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    sent_headers = {
        "Content-Type": "application/json",
        "User-Agent": "GitHub-Hookshot/test",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id or str(uuid.uuid4()),
        "X-Hub-Signature-256": signature or compute_signature(secret, body),
    }
    sent_headers.update(headers or {})
    return client.post(URL, content=body, headers=sent_headers)


@pytest.fixture
def make_client(api_settings: Settings) -> Iterator[Any]:
    clients: list[TestClient] = []

    def _make(settings: Settings | None = None) -> TestClient:
        client = TestClient(create_app(settings or api_settings), raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Any, respx_mock: respx.MockRouter) -> Iterator[TestClient]:
    """respx_mock is active so that ANY outbound HTTP call from the webhook would fail the
    test: intake must never talk to GitHub (or an LLM)."""
    yield make_client()
    assert respx_mock.calls.call_count == 0


async def count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def all_rows[T](session: AsyncSession, model: type[T]) -> list[T]:
    """Fresh rows (populate_existing overwrites objects this session loaded earlier)."""
    return list(
        (await session.scalars(select(model), execution_options={"populate_existing": True})).all()
    )


async def runs_by_status(session: AsyncSession) -> list[str]:
    rows = await session.scalars(
        select(ReviewRun.status).order_by(ReviewRun.created_at),
    )
    return list(rows.all())


# --- signature and request validation --------------------------------------------------------


async def test_valid_signature_is_accepted_and_the_delivery_recorded(
    client: TestClient, session: AsyncSession
) -> None:
    response = deliver(client, "ping", fixture("ping"), delivery_id="d-ping-1")
    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "reason": "event 'ping' is not handled"}

    [delivery] = await all_rows(session, WebhookDelivery)
    assert (delivery.delivery_id, delivery.event, delivery.action) == ("d-ping-1", "ping", None)


async def test_tampered_body_is_rejected_and_nothing_is_recorded(
    client: TestClient, session: AsyncSession
) -> None:
    original = json.dumps(fixture("pull_request.opened")).encode()
    signature = compute_signature(TEST_WEBHOOK_SECRET, original)
    tampered = original.replace(b"demo-contributor", b"evil-attacker")

    response = deliver(client, "pull_request", tampered, signature=signature)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"
    assert await count(session, WebhookDelivery) == 0
    assert await count(session, Job) == 0


async def test_missing_signature_is_rejected(client: TestClient, session: AsyncSession) -> None:
    body = json.dumps(fixture("pull_request.opened")).encode()
    response = client.post(
        URL,
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-1"},
    )
    assert response.status_code == 401
    assert await count(session, WebhookDelivery) == 0


async def test_signature_made_with_another_secret_is_rejected(client: TestClient) -> None:
    response = deliver(client, "ping", fixture("ping"), secret="some-other-secret-value-123")
    assert response.status_code == 401


@pytest.mark.parametrize("missing", ["X-GitHub-Delivery", "X-GitHub-Event"])
async def test_signed_request_without_github_headers_is_a_bad_request(
    client: TestClient, session: AsyncSession, missing: str
) -> None:
    response = deliver(client, "ping", fixture("ping"), headers={missing: ""})
    assert response.status_code == 400
    assert await count(session, WebhookDelivery) == 0


async def test_signed_non_json_body_is_a_bad_request(client: TestClient) -> None:
    response = deliver(client, "ping", b"payload=%7B%7D")  # form-encoded webhook config
    assert response.status_code == 400
    assert "JSON" in response.json()["error"]["message"]


async def test_oversized_body_is_refused_before_anything_else(
    client: TestClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks_api, "MAX_BODY_BYTES", 1000)
    response = deliver(client, "ping", b"x" * 1001)
    assert response.status_code == 413
    assert await count(session, WebhookDelivery) == 0


async def test_no_csrf_header_or_cookie_is_needed(client: TestClient) -> None:
    # GitHub sends neither; the CSRF middleware exempts exactly this path.
    response = deliver(client, "ping", fixture("ping"))
    assert response.status_code == 202


# --- idempotency -----------------------------------------------------------------------------


async def test_duplicate_delivery_returns_200_and_does_nothing(
    client: TestClient, session: AsyncSession
) -> None:
    payload = fixture("pull_request.opened")
    first = deliver(client, "pull_request", payload, delivery_id="d-dup")
    second = deliver(client, "pull_request", payload, delivery_id="d-dup")

    assert first.status_code == 202
    assert first.json() == {"status": "queued"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert await count(session, WebhookDelivery) == 1
    assert await count(session, ReviewRun) == 1
    assert await count(session, Job) == 1


async def test_malformed_payload_rolls_back_so_a_redelivery_still_works(
    client: TestClient, session: AsyncSession
) -> None:
    broken = fixture("pull_request.opened")
    del broken["pull_request"]["head"]
    response = deliver(client, "pull_request", broken, delivery_id="d-retry")
    assert response.status_code == 400
    assert await count(session, WebhookDelivery) == 0  # not marked as seen

    redelivery = deliver(
        client, "pull_request", fixture("pull_request.opened"), delivery_id="d-retry"
    )
    assert redelivery.status_code == 202
    assert await count(session, ReviewRun) == 1


# --- pull_request ----------------------------------------------------------------------------


async def test_pull_request_opened_creates_rows_and_a_review_job(
    client: TestClient, session: AsyncSession
) -> None:
    payload = fixture("pull_request.opened")
    response = deliver(client, "pull_request", payload)
    assert response.status_code == 202

    [installation] = await all_rows(session, Installation)
    assert (installation.github_installation_id, installation.account_login) == (3003, "demo-owner")
    assert installation.account_type == "user"
    [repo] = await all_rows(session, Repository)
    assert (repo.github_repo_id, repo.full_name, repo.default_branch) == (
        2002,
        "demo-owner/codelens-demo",
        "main",
    )
    [pr] = await all_rows(session, PullRequest)
    assert (pr.number, pr.author_login, pr.state) == (1, "demo-contributor", "open")
    assert pr.title == "Add input validation to the signup form"
    [run] = await all_rows(session, ReviewRun)
    assert (run.pull_request_id, run.status) == (pr.id, "queued")
    [job] = await all_rows(session, Job)
    assert (job.type, job.status) == ("review_pr", "queued")
    assert job.payload == {
        "review_run_id": str(run.id),
        "head_sha": payload["pull_request"]["head"]["sha"],
    }


@pytest.mark.parametrize("action", ["reopened", "ready_for_review", "synchronize"])
async def test_other_review_actions_queue_a_run(
    client: TestClient, session: AsyncSession, action: str
) -> None:
    payload = fixture("pull_request.opened") | {"action": action}
    assert deliver(client, "pull_request", payload).json() == {"status": "queued"}
    assert await runs_by_status(session) == ["queued"]


async def test_new_push_supersedes_queued_and_running_runs_only(
    client: TestClient, session: AsyncSession
) -> None:
    deliver(client, "pull_request", fixture("pull_request.opened"))
    await session.execute(update(ReviewRun).values(status="succeeded"))  # run 1 finished
    await session.commit()

    deliver(client, "pull_request", fixture("pull_request.opened") | {"action": "reopened"})
    await session.execute(
        update(ReviewRun).where(ReviewRun.status == "queued").values(status="running")
    )  # run 2 is being worked on
    await session.commit()
    deliver(client, "pull_request", fixture("pull_request.opened") | {"action": "reopened"})
    # run 3 is still queued when the next push arrives

    deliver(client, "pull_request", fixture("pull_request.synchronize"))  # run 4

    assert await runs_by_status(session) == ["succeeded", "superseded", "superseded", "queued"]
    runs = await all_rows(session, ReviewRun)
    assert all(run.finished_at is not None for run in runs if run.status == "superseded")
    jobs = await all_rows(session, Job)
    newest = max(jobs, key=lambda j: j.created_at)
    assert newest.payload["head_sha"] == fixture("pull_request.synchronize")["after"]


async def test_supersede_only_touches_the_same_pull_request(
    client: TestClient, session: AsyncSession
) -> None:
    deliver(client, "pull_request", fixture("pull_request.opened"))
    other = fixture("pull_request.opened")
    other["pull_request"]["number"] = 2
    deliver(client, "pull_request", other)
    assert await runs_by_status(session) == ["queued", "queued"]


async def test_draft_pull_request_is_recorded_but_not_reviewed(
    client: TestClient, session: AsyncSession
) -> None:
    response = deliver(client, "pull_request", fixture("pull_request.opened.draft"))
    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "reason": "draft pull request"}
    assert await count(session, PullRequest) == 1
    assert await count(session, ReviewRun) == 0
    assert await count(session, Job) == 0
    assert await count(session, WebhookDelivery) == 1


async def test_bot_authors_are_skipped_by_default(
    client: TestClient, session: AsyncSession
) -> None:
    payload = fixture("pull_request.opened")
    payload["pull_request"]["user"] = {"login": "dependabot[bot]", "id": 49699333, "type": "Bot"}
    response = deliver(client, "pull_request", payload)
    assert response.json()["reason"] == "pull request author is ignored"
    assert await count(session, Job) == 0


async def test_ignored_authors_are_configurable(make_client: Any, session: AsyncSession) -> None:
    settings = make_settings(
        database_url=str(session.bind.url.render_as_string(hide_password=False)),
        webhook_ignored_pr_authors=frozenset({"demo-contributor"}),
    )
    client = make_client(settings)
    assert deliver(client, "pull_request", fixture("pull_request.opened")).json()["status"] == (
        "ignored"
    )
    payload = fixture("pull_request.opened")
    payload["pull_request"]["user"] = {"login": "renovate[bot]", "id": 1, "type": "Bot"}
    payload["pull_request"]["number"] = 2
    # The default list no longer applies once it is overridden.
    assert deliver(client, "pull_request", payload).json() == {"status": "queued"}


async def test_closed_merged_updates_the_pr_without_a_run(
    client: TestClient, session: AsyncSession
) -> None:
    deliver(client, "pull_request", fixture("pull_request.opened"))
    response = deliver(client, "pull_request", fixture("pull_request.closed.merged"))
    assert response.json() == {"status": "recorded"}
    [pr] = await all_rows(session, PullRequest)
    assert pr.state == "merged"
    assert await count(session, ReviewRun) == 1


async def test_unhandled_pull_request_action_is_ignored(
    client: TestClient, session: AsyncSession
) -> None:
    payload = fixture("pull_request.opened") | {"action": "labeled"}
    response = deliver(client, "pull_request", payload)
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert await count(session, PullRequest) == 0
    [delivery] = await all_rows(session, WebhookDelivery)
    assert delivery.action == "labeled"


# --- push ------------------------------------------------------------------------------------


async def test_push_to_default_branch_enqueues_index_repo(
    client: TestClient, session: AsyncSession
) -> None:
    payload = fixture("push.default_branch")
    assert deliver(client, "push", payload).json() == {"status": "queued"}

    [repo] = await all_rows(session, Repository)
    assert repo.index_status == "queued"
    [job] = await all_rows(session, Job)
    assert job.type == "index_repo"
    assert job.payload == {"repository_id": str(repo.id), "commit_sha": payload["after"]}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"ref": "refs/heads/feature/validation"}, "not the default branch"),
        ({"ref": "refs/tags/v1.0.0"}, "not the default branch"),
        ({"deleted": True, "after": "0" * 40}, "branch deleted"),
    ],
)
async def test_other_pushes_are_ignored(
    client: TestClient, session: AsyncSession, changes: dict[str, Any], reason: str
) -> None:
    response = deliver(client, "push", fixture("push.default_branch") | changes)
    assert response.json() == {"status": "ignored", "reason": reason}
    assert await count(session, Job) == 0


async def test_push_corrects_a_placeholder_default_branch(
    client: TestClient, session: AsyncSession
) -> None:
    deliver(client, "installation", fixture("installation.created"))
    [repo] = await all_rows(session, Repository)
    assert repo.default_branch == "main"  # placeholder: installation payloads omit it

    payload = fixture("push.default_branch")
    payload["ref"] = "refs/heads/trunk"
    payload["repository"]["default_branch"] = "trunk"
    assert deliver(client, "push", payload).json() == {"status": "queued"}
    [repo] = await all_rows(session, Repository)
    assert repo.default_branch == "trunk"


# --- installation / installation_repositories ------------------------------------------------


async def test_installation_created_upserts_installation_and_repositories(
    client: TestClient, session: AsyncSession
) -> None:
    response = deliver(client, "installation", fixture("installation.created"))
    assert response.json() == {"status": "recorded"}
    [installation] = await all_rows(session, Installation)
    assert installation.github_installation_id == 3003
    assert installation.uninstalled_at is None
    [repo] = await all_rows(session, Repository)
    assert (repo.github_repo_id, repo.installation_id) == (2002, installation.id)


async def test_installation_deleted_marks_it_and_keeps_review_history(
    client: TestClient, session: AsyncSession
) -> None:
    deliver(client, "installation", fixture("installation.created"))
    deliver(client, "pull_request", fixture("pull_request.opened"))

    response = deliver(client, "installation", fixture("installation.deleted"))
    assert response.json() == {"status": "queued"}

    [installation] = await all_rows(session, Installation)
    assert installation.uninstalled_at is not None
    # ADR 0001: nothing is deleted by the webhook.
    assert await count(session, Repository) == 1
    assert await count(session, PullRequest) == 1
    assert await count(session, ReviewRun) == 1
    purge = [job for job in await all_rows(session, Job) if job.type == "purge_installation"]
    assert [job.payload for job in purge] == [{"installation_id": str(installation.id)}]


async def test_suspend_is_recorded_but_not_acted_on(
    client: TestClient, session: AsyncSession
) -> None:
    payload = fixture("installation.created") | {"action": "suspend"}
    response = deliver(client, "installation", payload)
    assert response.json()["status"] == "ignored"
    assert await count(session, Installation) == 0
    assert await count(session, WebhookDelivery) == 1


async def test_repositories_added_then_removed(client: TestClient, session: AsyncSession) -> None:
    deliver(client, "installation", fixture("installation.created"))
    assert deliver(
        client, "installation_repositories", fixture("installation_repositories.added")
    ).json() == {"status": "recorded"}
    repos = {r.github_repo_id: r for r in await all_rows(session, Repository)}
    assert set(repos) == {2002, 2003}
    assert repos[2003].is_private is True

    # A signed-in user who could see both repos.
    user = User(github_id=1001, login="demo-owner")
    session.add(user)
    await session.flush()
    session.add_all(
        [
            RepositoryAccess(user_id=user.id, repository_id=repos[2002].id),
            RepositoryAccess(user_id=user.id, repository_id=repos[2003].id),
        ]
    )
    await session.commit()

    deliver(client, "installation_repositories", fixture("installation_repositories.removed"))

    remaining = await all_rows(session, RepositoryAccess)
    assert [a.repository_id for a in remaining] == [repos[2002].id]
    assert await count(session, Repository) == 2  # the removed repo's history is kept


async def test_unknown_event_is_recorded_and_ignored(
    client: TestClient, session: AsyncSession
) -> None:
    response = deliver(client, "issues", {"action": "opened"})
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert await count(session, WebhookDelivery) == 1
