"""Authorization boundaries: roles inside an installation, and isolation between them.

Cast (all from the fake GitHub, so roles come from the real login mapping):
- alice: org admin of acme (admin), sees acme/api and acme/secret
- bob:   plain org member of acme (member), sees only acme/api
- carol: owner of her personal installation; no access to acme at all
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Installation, Repository
from app.deps import require_repository_access
from tests.conftest import OpenBrowser
from tests.fake_github import FakeGitHub, FakeInstallation, FakeRepo, FakeUser, sign_in

ACME = FakeInstallation(id=200, account_login="acme", account_id=50, account_type="Organization")
CAROLS = FakeInstallation(id=300, account_login="carol", account_id=3)
API, SECRET, CAROL_REPO = (
    FakeRepo(600, "acme/api"),
    FakeRepo(601, "acme/secret"),
    FakeRepo(700, "carol/x"),
)


def _with_repo_probe(app: FastAPI) -> None:
    """Repo endpoints arrive in M6; until then a test-only route exercises the guard."""

    @app.get("/_test/repos/{repository_id}")
    async def probe(
        repo: Annotated[Repository, Depends(require_repository_access)],
    ) -> dict[str, str]:
        return {"full_name": repo.full_name}


@dataclass
class Cast:
    alice: TestClient
    bob: TestClient
    carol: TestClient
    acme_id: uuid.UUID
    carol_inst_id: uuid.UUID
    repo_ids: dict[str, uuid.UUID]


async def _cast(open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession) -> Cast:
    users = {
        "alice": FakeUser(id=1, login="alice", org_roles={"acme": "admin"}).grant(
            ACME, API, SECRET
        ),
        "bob": FakeUser(id=2, login="bob", org_roles={"acme": "member"}).grant(ACME, API),
        "carol": FakeUser(id=3, login="carol").grant(CAROLS, CAROL_REPO),
    }
    clients = {}
    for name, user in users.items():
        clients[name] = open_browser(customize=_with_repo_probe)
        assert sign_in(clients[name], github.add(user)).status_code == 302

    installations = {
        i.github_installation_id: i.id for i in await session.scalars(select(Installation))
    }
    repos = {r.full_name: r.id for r in await session.scalars(select(Repository))}
    return Cast(
        alice=clients["alice"],
        bob=clients["bob"],
        carol=clients["carol"],
        acme_id=installations[ACME.id],
        carol_inst_id=installations[CAROLS.id],
        repo_ids=repos,
    )


# --- roles inside one installation ---------------------------------------------------------


async def test_member_cannot_call_the_admin_endpoint(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)

    response = cast.bob.get(f"/installations/{cast.acme_id}/members")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_admin_can_call_the_admin_endpoint(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)

    response = cast.alice.get(f"/installations/{cast.acme_id}/members")

    assert response.status_code == 200
    assert {(m["login"], m["role"]) for m in response.json()} == {
        ("alice", "admin"),
        ("bob", "member"),
    }


async def test_member_can_read_their_installation(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)

    response = cast.bob.get(f"/installations/{cast.acme_id}")

    assert response.status_code == 200
    assert response.json()["role"] == "member"


async def test_owner_passes_admin_checks(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)
    assert cast.carol.get(f"/installations/{cast.carol_inst_id}/members").status_code == 200


# --- isolation between installations -------------------------------------------------------


async def test_user_cannot_read_another_installations_data(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)

    # 404, not 403: an outsider learns nothing, not even that the installation exists.
    for path in (f"/installations/{cast.acme_id}", f"/installations/{cast.acme_id}/members"):
        response = cast.carol.get(path)
        assert response.status_code == 404, path
    assert cast.alice.get(f"/installations/{cast.carol_inst_id}").status_code == 404


async def test_unknown_installation_looks_the_same_as_a_foreign_one(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)
    foreign = cast.carol.get(f"/installations/{cast.acme_id}")
    unknown = cast.carol.get(f"/installations/{uuid.uuid4()}")
    assert (foreign.status_code, foreign.json()) == (unknown.status_code, unknown.json())


async def test_me_lists_only_the_users_own_installations(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)

    body = cast.carol.get("/me").json()

    assert body["user"]["login"] == "carol"
    assert [(i["account_login"], i["role"]) for i in body["installations"]] == [("carol", "owner")]


async def test_uninstalled_installation_disappears(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)
    await session.execute(
        update(Installation)
        .where(Installation.id == cast.acme_id)
        .values(uninstalled_at=Installation.created_at)
    )
    await session.commit()

    assert cast.alice.get(f"/installations/{cast.acme_id}").status_code == 404
    assert cast.alice.get("/me").json()["installations"] == []


def test_admin_endpoint_requires_a_session(open_browser: OpenBrowser) -> None:
    assert open_browser().get(f"/installations/{uuid.uuid4()}/members").status_code == 401


# --- repository access (second level) ------------------------------------------------------


async def test_repository_access_needs_both_membership_and_an_access_row(
    open_browser: OpenBrowser, github: FakeGitHub, session: AsyncSession
) -> None:
    cast = await _cast(open_browser, github, session)
    secret = cast.repo_ids["acme/secret"]
    api = cast.repo_ids["acme/api"]

    assert cast.alice.get(f"/_test/repos/{secret}").json() == {"full_name": "acme/secret"}
    assert cast.bob.get(f"/_test/repos/{api}").status_code == 200
    # Bob is an acme member but GitHub does not give him acme/secret.
    assert cast.bob.get(f"/_test/repos/{secret}").status_code == 404
    # Carol is not in acme at all.
    assert cast.carol.get(f"/_test/repos/{api}").status_code == 404
