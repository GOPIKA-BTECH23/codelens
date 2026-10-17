"""The M6 REST endpoints: authorization on every one, then behaviour.

Cast (same as test_rbac, logged in through the fake GitHub so roles come from the real mapping):
- alice: org admin of acme; sees acme/api and acme/secret
- bob:   plain org member of acme; sees only acme/api (no repository_access for acme/secret)
- carol: owner of her own installation; no access to acme at all
- anon:  not signed in
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Finding,
    Installation,
    Job,
    PullRequest,
    Repository,
    ReviewRun,
    ReviewRunFile,
)
from app.main import create_app
from tests.app_utils import make_settings
from tests.conftest import OpenBrowser
from tests.fake_github import FakeGitHub, FakeUser, sign_in
from tests.integration.test_rbac import ACME, API, CAROL_REPO, CAROLS, SECRET

Factory = async_sessionmaker[AsyncSession]
PATCH = "@@ -1,2 +1,3 @@ def f():\n a = 1\n-b = 2\n+b = db.execute(f'select {x}')\n+c = 3"


@dataclass
class World:
    alice: TestClient
    bob: TestClient
    carol: TestClient
    anon: TestClient
    acme_id: uuid.UUID
    carol_inst_id: uuid.UUID
    repo: dict[str, uuid.UUID]  # "api" / "secret" / "carol"
    pull: dict[str, uuid.UUID]
    finding: dict[str, uuid.UUID]
    run: dict[str, uuid.UUID]

    def client(self, who: str) -> TestClient:
        return {"alice": self.alice, "bob": self.bob, "carol": self.carol, "anon": self.anon}[who]


async def seed_review(
    session_factory: Factory, repository_id: uuid.UUID, number: int = 1, *, with_patch: bool = True
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """One PR with one succeeded run, one finding and one stored file. Returns ids."""
    async with session_factory.begin() as s:
        pull = PullRequest(
            repository_id=repository_id, number=number, title=f"PR {number}", author_login="dev"
        )
        s.add(pull)
        await s.flush()
        run = ReviewRun(
            pull_request_id=pull.id,
            status="succeeded",
            model="m",
            prompt_version="security_v1",
            tokens_in=100,
            tokens_out=50,
            cost_usd=Decimal("0.01"),
            latency_ms=1000,
            head_sha="a" * 40,
            summary={
                "what_changed": "Adds a query.",
                "notes": ["Reviewed 1 file(s) in 1 part(s)."],
            },
        )
        s.add(run)
        await s.flush()
        finding = Finding(
            review_run_id=run.id,
            file="app/db.py",
            line=3,
            severity="high",
            category="sql-injection",
            message="Input is concatenated.",
            suggestion="Use parameters.",
            confidence=0.9,
        )
        s.add(finding)
        s.add(
            ReviewRunFile(
                review_run_id=run.id,
                path="app/db.py",
                status="modified",
                additions=2,
                deletions=1,
                patch=PATCH if with_patch else None,
            )
        )
        await s.flush()
        return pull.id, run.id, finding.id


@pytest.fixture
async def world(open_browser: OpenBrowser, github: FakeGitHub, session_factory: Factory) -> World:
    users = {
        "alice": FakeUser(id=1, login="alice", org_roles={"acme": "admin"}).grant(
            ACME, API, SECRET
        ),
        "bob": FakeUser(id=2, login="bob", org_roles={"acme": "member"}).grant(ACME, API),
        "carol": FakeUser(id=3, login="carol").grant(CAROLS, CAROL_REPO),
    }
    clients: dict[str, TestClient] = {}
    for name, user in users.items():
        clients[name] = open_browser()
        assert sign_in(clients[name], github.add(user)).status_code == 302

    async with session_factory() as s:
        installs = {i.github_installation_id: i.id for i in await s.scalars(select(Installation))}
        repos = {r.full_name: r.id for r in await s.scalars(select(Repository))}
    repo = {"api": repos["acme/api"], "secret": repos["acme/secret"], "carol": repos["carol/x"]}
    pull, run, finding = {}, {}, {}
    for key, repo_id in repo.items():
        pull[key], run[key], finding[key] = await seed_review(session_factory, repo_id)
    return World(
        alice=clients["alice"],
        bob=clients["bob"],
        carol=clients["carol"],
        anon=open_browser(),
        acme_id=installs[ACME.id],
        carol_inst_id=installs[CAROLS.id],
        repo=repo,
        pull=pull,
        finding=finding,
        run=run,
    )


# --- authorization matrix -------------------------------------------------------------------
# One row per endpoint x caller: the status every caller must get. 401 anon, 404 for anything
# the caller cannot see (never 403: existence is not confirmed), 403 only for "visible but not
# allowed".

ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, dict[str, int]]] = [
    # method, path, json body, expected status per caller
    ("GET", "/repos", None, {"alice": 200, "bob": 200, "carol": 200, "anon": 401}),
    ("GET", "/repos/{repo.api}", None, {"alice": 200, "bob": 200, "carol": 404, "anon": 401}),
    ("GET", "/repos/{repo.secret}", None, {"alice": 200, "bob": 404, "carol": 404, "anon": 401}),
    ("GET", "/repos/{repo.api}/pulls", None, {"alice": 200, "bob": 200, "carol": 404, "anon": 401}),
    (
        "GET",
        "/repos/{repo.secret}/pulls",
        None,
        {"alice": 200, "bob": 404, "carol": 404, "anon": 401},
    ),
    (
        "POST",
        "/repos/{repo.api}/index",
        None,
        {"alice": 202, "bob": 403, "carol": 404, "anon": 401},
    ),
    (
        "POST",
        "/repos/{repo.secret}/index",
        None,
        {"alice": 202, "bob": 404, "carol": 404, "anon": 401},
    ),
    (
        "PATCH",
        "/repos/{repo.api}/settings",
        {"severity_threshold": "high"},
        {"alice": 200, "bob": 403, "carol": 404, "anon": 401},
    ),
    (
        "PATCH",
        "/repos/{repo.secret}/settings",
        {"severity_threshold": "high"},
        {"alice": 200, "bob": 404, "carol": 404, "anon": 401},
    ),
    ("GET", "/pulls/{pull.api}", None, {"alice": 200, "bob": 200, "carol": 404, "anon": 401}),
    ("GET", "/pulls/{pull.secret}", None, {"alice": 200, "bob": 404, "carol": 404, "anon": 401}),
    (
        "GET",
        "/pulls/{pull.api}/review",
        None,
        {"alice": 200, "bob": 200, "carol": 404, "anon": 401},
    ),
    (
        "GET",
        "/pulls/{pull.secret}/review",
        None,
        {"alice": 200, "bob": 404, "carol": 404, "anon": 401},
    ),
    (
        "PATCH",
        "/findings/{finding.api}",
        {"dismissed": True},
        {"alice": 200, "bob": 200, "carol": 404, "anon": 401},
    ),
    (
        "PATCH",
        "/findings/{finding.secret}",
        {"dismissed": True},
        {"alice": 200, "bob": 404, "carol": 404, "anon": 401},
    ),
    ("GET", "/analytics/usage", None, {"alice": 200, "bob": 403, "carol": 200, "anon": 401}),
]


class _Ids:
    """Lets the table above use "{repo.api}" placeholders."""

    def __init__(self, ids: dict[str, uuid.UUID]) -> None:
        self.__dict__.update(ids)


@pytest.mark.parametrize("who", ["alice", "bob", "carol", "anon"])
@pytest.mark.parametrize(
    ("method", "path", "body", "expected"), ENDPOINTS, ids=[f"{e[0]} {e[1]}" for e in ENDPOINTS]
)
async def test_every_endpoint_enforces_its_role_boundary(
    world: World,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected: dict[str, int],
    who: str,
) -> None:
    url = path.format(repo=_Ids(world.repo), pull=_Ids(world.pull), finding=_Ids(world.finding))
    response = world.client(who).request(method, url, json=body)
    assert response.status_code == expected[who], response.text
    if expected[who] in (401, 403, 404):
        assert response.json()["error"]["code"] in ("unauthorized", "forbidden", "not_found")


async def test_unknown_ids_look_exactly_like_other_peoples_ids(world: World) -> None:
    missing = uuid.uuid4()
    for path in (f"/repos/{missing}", f"/pulls/{missing}", f"/pulls/{missing}/review"):
        assert world.alice.get(path).status_code == 404
    assert world.alice.patch(f"/findings/{missing}", json={"dismissed": True}).status_code == 404


async def test_repo_list_shows_only_what_each_caller_may_see(world: World) -> None:
    names = lambda c: sorted(i["full_name"] for i in c.get("/repos").json()["items"])  # noqa: E731
    assert names(world.alice) == ["acme/api", "acme/secret"]
    assert names(world.bob) == ["acme/api"]
    assert names(world.carol) == ["carol/x"]


async def test_analytics_only_counts_the_callers_own_installations(world: World) -> None:
    alice = world.alice.get("/analytics/usage").json()["totals"]
    carol = world.carol.get("/analytics/usage").json()["totals"]
    assert alice["runs"] == 2  # acme/api + acme/secret, never carol's
    assert carol["runs"] == 1
    # Naming someone else's installation: not a member -> 404; member but not admin -> 403.
    assert world.carol.get(f"/analytics/usage?installation_id={world.acme_id}").status_code == 404
    assert world.bob.get(f"/analytics/usage?installation_id={world.acme_id}").status_code == 403


async def test_state_changing_requests_need_the_csrf_header(
    world: World, open_browser: OpenBrowser, github: FakeGitHub
) -> None:
    bare = open_browser(csrf_header=False)
    bare.cookies.update(world.alice.cookies)
    response = bare.patch(f"/findings/{world.finding['api']}", json={"dismissed": True})
    assert response.status_code == 403


# --- repos ----------------------------------------------------------------------------------


async def test_repo_list_is_paginated_and_carries_the_latest_run(
    world: World, session_factory: Factory
) -> None:
    page = world.alice.get("/repos?limit=1&offset=0").json()
    assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (2, 1, 0, 1)
    second = world.alice.get("/repos?limit=1&offset=1").json()
    assert second["items"][0]["id"] != page["items"][0]["id"]
    api = next(i for i in world.alice.get("/repos").json()["items"] if i["full_name"] == "acme/api")
    assert api["role"] == "admin" and api["installation_login"] == "acme"
    assert api["latest_run"]["status"] == "succeeded" and api["latest_run"]["pull_number"] == 1
    assert world.alice.get("/repos?q=SEC").json()["total"] == 1
    assert world.alice.get("/repos?q=%25").json()["total"] == 0  # LIKE wildcards are escaped


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=x"])
async def test_pagination_bounds_are_validated(world: World, query: str) -> None:
    assert world.alice.get(f"/repos?{query}").status_code == 422


async def test_index_request_enqueues_once_and_is_idempotent(
    world: World, session_factory: Factory
) -> None:
    url = f"/repos/{world.repo['api']}/index"
    first = world.alice.post(url)
    again = world.alice.post(url)
    assert first.json() == {"index_status": "queued", "enqueued": True}
    assert again.json() == {"index_status": "queued", "enqueued": False}
    async with session_factory() as s:
        jobs = (await s.scalars(select(Job).where(Job.type == "index_repo"))).all()
        repo = await s.get(Repository, world.repo["api"])
    assert len(jobs) == 1 and jobs[0].payload["repository_id"] == str(world.repo["api"])
    assert repo is not None and repo.index_status == "queued"


async def test_settings_default_merge_and_validation(world: World) -> None:
    url = f"/repos/{world.repo['api']}"
    assert world.alice.get(url).json()["settings"] == {
        "enabled_agents": ["security"],
        "severity_threshold": "info",
        "ignore_paths": [],
    }
    changed = world.alice.patch(f"{url}/settings", json={"severity_threshold": "medium"})
    assert changed.json()["severity_threshold"] == "medium"
    world.alice.patch(f"{url}/settings", json={"ignore_paths": ["docs/**"]})
    settings = world.bob.get(url).json()["settings"]  # a member can read them
    assert settings["severity_threshold"] == "medium"  # the first change survived the second
    assert settings["ignore_paths"] == ["docs/**"]
    for bad in (
        {"severity_threshold": "apocalyptic"},
        {"enabled_agents": ["style"]},
        {"unknown": 1},
        {"ignore_paths": ["x"] * 51},
    ):
        assert world.alice.patch(f"{url}/settings", json=bad).status_code == 422


# --- pulls and the review page --------------------------------------------------------------


async def test_review_posted_follows_github_review_id(
    world: World, session_factory: Factory
) -> None:
    """A run that succeeded without posting a review (the M4 stub) is not "reviewed"."""
    repo, pull = world.repo["api"], world.pull["api"]

    def flags() -> tuple[bool, bool, bool]:
        listed = next(i for i in world.alice.get("/repos").json()["items"] if i["id"] == str(repo))
        pulls = world.alice.get(f"/repos/{repo}/pulls").json()["items"]
        return (
            listed["latest_run"]["review_posted"],
            pulls[0]["latest_run"]["review_posted"],
            world.alice.get(f"/pulls/{pull}/review").json()["run"]["review_posted"],
        )

    assert flags() == (False, False, False)
    async with session_factory.begin() as s:
        run = (
            await s.execute(select(ReviewRun).where(ReviewRun.pull_request_id == pull))
        ).scalar_one()
        run.github_review_id = 1234
    assert flags() == (True, True, True)


async def test_pull_list_filters_by_state_and_shows_open_findings(
    world: World, session_factory: Factory
) -> None:
    async with session_factory.begin() as s:
        s.add(
            PullRequest(
                repository_id=world.repo["api"],
                number=2,
                title="old",
                author_login="dev",
                state="merged",
            )
        )
    everything = world.alice.get(f"/repos/{world.repo['api']}/pulls").json()
    assert everything["total"] == 2
    merged = world.alice.get(f"/repos/{world.repo['api']}/pulls?state=merged").json()
    assert [p["number"] for p in merged["items"]] == [2] and merged["items"][0][
        "latest_run"
    ] is None
    reviewed = next(p for p in everything["items"] if p["number"] == 1)
    assert reviewed["latest_run"]["findings_count"] == 1

    world.alice.patch(f"/findings/{world.finding['api']}", json={"dismissed": True})
    again = world.alice.get(f"/repos/{world.repo['api']}/pulls").json()
    assert next(p for p in again["items"] if p["number"] == 1)["latest_run"]["findings_count"] == 0


async def test_review_payload_has_run_metadata_summary_findings_and_diff(world: World) -> None:
    body = world.bob.get(f"/pulls/{world.pull['api']}/review").json()
    assert body["run"]["model"] == "m" and body["run"]["prompt_version"] == "security_v1"
    assert body["run"]["cost_usd"] == pytest.approx(0.01) and body["run"]["latency_ms"] == 1000
    assert body["summary"]["what_changed"] == "Adds a query."
    assert [f["category"] for f in body["findings"]] == ["sql-injection"]
    (file,) = body["files"]
    assert file["path"] == "app/db.py" and file["patch_available"] is True
    lines = file["hunks"][0]["lines"]
    assert [(ln["kind"], ln["old_line"], ln["new_line"]) for ln in lines] == [
        (" ", 1, 1),
        ("-", 2, None),
        ("+", None, 2),
        ("+", None, 3),
    ]


async def test_review_is_null_before_the_first_run_and_flags_a_missing_patch(
    world: World, session_factory: Factory
) -> None:
    async with session_factory.begin() as s:
        fresh = PullRequest(
            repository_id=world.repo["api"], number=9, title="new", author_login="dev"
        )
        s.add(fresh)
        await s.flush()
        fresh_id = fresh.id
    assert world.alice.get(f"/pulls/{fresh_id}/review").json() is None

    pull_id, _, _ = await seed_review(session_factory, world.repo["api"], 10, with_patch=False)
    (file,) = world.alice.get(f"/pulls/{pull_id}/review").json()["files"]
    assert file["patch_available"] is False and file["hunks"] == []


async def test_the_review_shows_the_latest_run_only(world: World, session_factory: Factory) -> None:
    async with session_factory.begin() as s:
        s.add(
            ReviewRun(
                pull_request_id=world.pull["api"],
                status="failed",
                error="boom",
                created_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
    body = world.alice.get(f"/pulls/{world.pull['api']}/review").json()
    assert body["run"]["status"] == "failed" and body["run"]["error"] == "boom"
    assert body["findings"] == []


# --- findings -------------------------------------------------------------------------------


async def test_feedback_can_be_set_changed_and_cleared(world: World) -> None:
    url = f"/findings/{world.finding['api']}"
    assert (
        world.bob.patch(url, json={"user_feedback": "helpful"}).json()["user_feedback"] == "helpful"
    )
    assert (
        world.bob.patch(url, json={"user_feedback": "not_helpful"}).json()["user_feedback"]
        == "not_helpful"
    )
    cleared = world.bob.patch(url, json={"user_feedback": None}).json()
    assert cleared["user_feedback"] is None
    # An update that does not mention a field leaves it alone.
    world.bob.patch(url, json={"user_feedback": "helpful"})
    kept = world.bob.patch(url, json={"dismissed": True}).json()
    assert kept["user_feedback"] == "helpful" and kept["dismissed"] is True
    assert world.bob.patch(url, json={"dismissed": False}).json()["dismissed"] is False


@pytest.mark.parametrize(
    "body", [{"user_feedback": "meh"}, {"severity": "low"}, {"dismissed": "x"}]
)
async def test_invalid_finding_updates_are_rejected(world: World, body: dict[str, Any]) -> None:
    assert world.alice.patch(f"/findings/{world.finding['api']}", json=body).status_code == 422


# --- analytics ------------------------------------------------------------------------------


async def test_usage_aggregates_cost_runs_and_acceptance_per_day(
    world: World, session_factory: Factory
) -> None:
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    two_days_ago = today - timedelta(days=2)
    async with session_factory.begin() as s:
        # Move every seeded run onto known days and give them known costs.
        runs = (await s.scalars(select(ReviewRun).order_by(ReviewRun.created_at))).all()
        for run in runs:
            run.created_at, run.cost_usd, run.latency_ms = two_days_ago, Decimal("0.5"), 2000
        runs[0].created_at, runs[0].cost_usd, runs[0].latency_ms = today, Decimal("1.5"), 4000
    # Votes: both votes on acme/api's run, which sits in alice's scope.
    api_finding = world.finding["api"]
    world.alice.patch(f"/findings/{api_finding}", json={"user_feedback": "helpful"})
    async with session_factory.begin() as s:
        extra = Finding(
            review_run_id=world.run["api"], file="x", line=1, severity="low", category="c",
            message="m", confidence=0.5, user_feedback="not_helpful",
        )  # fmt: skip
        s.add(extra)

    body = world.alice.get(
        f"/analytics/usage?from={(today - timedelta(days=3)).date()}&to={today.date()}"
    ).json()
    assert len(body["days"]) == 4  # zero-filled
    assert body["totals"]["runs"] == 2
    assert body["totals"]["cost_usd"] == pytest.approx(2.0)
    assert body["totals"]["helpful"] == 1 and body["totals"]["not_helpful"] == 1
    assert body["totals"]["acceptance_rate"] == 0.5
    assert body["totals"]["latency_p50_ms"] == 3000
    by_day = {d["day"]: d for d in body["days"]}
    assert by_day[str(today.date())]["runs"] + by_day[str(two_days_ago.date())]["runs"] == 2
    assert by_day[str((today - timedelta(days=3)).date())]["runs"] == 0
    assert by_day[str((today - timedelta(days=3)).date())]["acceptance_rate"] is None


async def test_usage_range_is_validated(world: World) -> None:
    assert world.alice.get("/analytics/usage?from=2026-02-02&to=2026-01-01").status_code == 400
    assert world.alice.get("/analytics/usage?from=2020-01-01&to=2026-01-01").status_code == 400
    assert world.alice.get("/analytics/usage?from=nope").status_code == 422


def test_openapi_documents_the_new_endpoints() -> None:
    spec = create_app(make_settings()).openapi()
    for path in (
        "/repos",
        "/repos/{repository_id}/index",
        "/pulls/{pull_id}/review",
        "/findings/{finding_id}",
        "/analytics/usage",
    ):
        assert path in spec["paths"]
        for operation in spec["paths"][path].values():
            assert operation["summary"] and operation["responses"]
