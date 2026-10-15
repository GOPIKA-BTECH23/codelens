"""Pure RBAC rules: role ordering and the GitHub -> CodeLens role mapping (decision 12)."""

import pytest

from app.db.models.enums import MemberRole
from app.services.auth import role_for
from app.services.github_oauth import GitHubAccount, GitHubInstallation, GitHubUser
from app.services.rbac import role_at_least

OWNER, ADMIN, MEMBER = MemberRole.OWNER, MemberRole.ADMIN, MemberRole.MEMBER


@pytest.mark.parametrize(
    ("role", "minimum", "allowed"),
    [
        (OWNER, OWNER, True),
        (OWNER, ADMIN, True),
        (OWNER, MEMBER, True),
        (ADMIN, OWNER, False),
        (ADMIN, ADMIN, True),
        (ADMIN, MEMBER, True),
        (MEMBER, OWNER, False),
        (MEMBER, ADMIN, False),
        (MEMBER, MEMBER, True),
    ],
)
def test_role_ordering(role: MemberRole, minimum: MemberRole, allowed: bool) -> None:
    assert role_at_least(role, minimum) is allowed


def test_role_at_least_accepts_database_strings() -> None:
    assert role_at_least("admin", MEMBER)


def test_unknown_role_is_an_error_not_a_grant() -> None:
    with pytest.raises(ValueError):
        role_at_least("superuser", MEMBER)


USER = GitHubUser(id=1, login="alice")


def _inst(account_id: int, account_type: str) -> GitHubInstallation:
    return GitHubInstallation(
        id=10, account=GitHubAccount(id=account_id, login="acct", type=account_type)
    )


def test_personal_installation_owner() -> None:
    assert role_for(USER, _inst(1, "User"), None) is OWNER


def test_collaborator_on_someone_elses_personal_installation_is_member() -> None:
    assert role_for(USER, _inst(2, "User"), None) is MEMBER


@pytest.mark.parametrize(
    ("org_role", "expected"), [("admin", ADMIN), ("member", MEMBER), (None, MEMBER)]
)
def test_organisation_roles(org_role: str | None, expected: MemberRole) -> None:
    # Org owners (GitHub role "admin") are admins; nobody owns an org installation.
    assert role_for(USER, _inst(99, "Organization"), org_role) is expected  # type: ignore[arg-type]
