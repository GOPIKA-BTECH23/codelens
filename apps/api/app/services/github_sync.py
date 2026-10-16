"""Upserts of installations and repositories, shared by login sync (M2) and webhooks (M3).

INSERT ... ON CONFLICT DO UPDATE, keyed on GitHub's stable numeric ids (logins and repo
names can be renamed; ids cannot). updated_at is set explicitly because SQLAlchemy's
onupdate does not fire for the UPDATE half of an upsert. Only flushes; the caller commits.
"""

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Installation, Repository
from app.db.models.enums import AccountType

# GitHub's account "type" -> ours. Enterprise-level installations have neither and are
# out of scope.
ACCOUNT_TYPES = {"User": AccountType.USER, "Organization": AccountType.ORGANIZATION}


async def upsert_installation(
    session: AsyncSession,
    *,
    github_installation_id: int,
    account_login: str,
    account_type: AccountType,
) -> uuid.UUID:
    """Create or refresh an installation row; returns its id.

    uninstalled_at is deliberately NOT touched: only the `installation.deleted` webhook sets
    it, and GitHub gives a reinstall a new installation id, so ids are never reused.
    """
    values = {"account_login": account_login, "account_type": account_type.value}
    stmt = (
        insert(Installation)
        .values(github_installation_id=github_installation_id, **values)
        .on_conflict_do_update(
            index_elements=[Installation.github_installation_id],
            set_={**values, "updated_at": func.now()},
        )
        .returning(Installation.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def upsert_repository(
    session: AsyncSession,
    *,
    installation_id: uuid.UUID,
    github_repo_id: int,
    full_name: str,
    is_private: bool,
    default_branch: str | None,
) -> uuid.UUID:
    """Create or refresh a repository row; returns its id.

    `default_branch=None` means "the source did not say": installation webhooks list repos
    without it. A new row then gets the column default and an existing row keeps its value,
    until a pull_request or push payload (which carry it) corrects it.
    """
    values: dict[str, Any] = {
        "installation_id": installation_id,
        "full_name": full_name,
        "is_private": is_private,
    }
    if default_branch is not None:
        values["default_branch"] = default_branch
    stmt = (
        insert(Repository)
        .values(github_repo_id=github_repo_id, **values)
        .on_conflict_do_update(
            index_elements=[Repository.github_repo_id],
            set_={**values, "updated_at": func.now()},
        )
        .returning(Repository.id)
    )
    return (await session.execute(stmt)).scalar_one()
