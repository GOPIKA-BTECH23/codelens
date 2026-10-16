"""Constraints, cascades and search columns, checked against the real migrated schema."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.constants import EMBEDDING_DIM
from app.db.models import (
    ChatMessage,
    ChatSession,
    CodeChunk,
    Finding,
    Installation,
    InstallationMember,
    PullRequest,
    Repository,
    RepositoryAccess,
    ReviewRun,
    User,
    WebhookDelivery,
)


def _user(n: int = 1) -> User:
    return User(github_id=n, login=f"user{n}")


def _installation(n: int = 1) -> Installation:
    return Installation(github_installation_id=n, account_login=f"acct{n}", account_type="user")


def _repo(installation: Installation, n: int = 1) -> Repository:
    return Repository(installation_id=installation.id, github_repo_id=n, full_name=f"o/r{n}")


async def _seed_chain(session: AsyncSession) -> dict[str, object]:
    """installation -> repo -> PR -> run -> finding, plus a chunk and a chat."""
    user, inst = _user(), _installation()
    session.add_all([user, inst])
    await session.flush()
    repo = _repo(inst)
    session.add(repo)
    await session.flush()
    pr = PullRequest(repository_id=repo.id, number=1, title="t", author_login="a")
    session.add(pr)
    await session.flush()
    run = ReviewRun(pull_request_id=pr.id)
    chat = ChatSession(repository_id=repo.id, user_id=user.id)
    chunk = CodeChunk(
        repository_id=repo.id,
        path="a.py",
        start_line=1,
        end_line=2,
        content="def foo(): pass",
        embedding=[0.1] * EMBEDDING_DIM,
        commit_sha="a" * 40,
    )
    session.add_all([run, chat, chunk])
    await session.flush()
    session.add_all(
        [
            Finding(
                review_run_id=run.id,
                file="a.py",
                line=1,
                severity="low",
                category="style",
                confidence=0.9,
            ),
            ChatMessage(session_id=chat.id, role="user", content="hi"),
        ]
    )
    await session.commit()
    return {"user": user, "inst": inst, "repo": repo, "chat": chat}


async def test_webhook_delivery_id_is_unique(session: AsyncSession) -> None:
    session.add(WebhookDelivery(delivery_id="d-1", event="pull_request"))
    await session.commit()

    session.add(WebhookDelivery(delivery_id="d-1", event="pull_request"))
    with pytest.raises(IntegrityError, match="uq_webhook_deliveries_delivery_id"):
        await session.commit()


async def test_installation_member_pair_is_unique(session: AsyncSession) -> None:
    user, inst = _user(), _installation()
    session.add_all([user, inst])
    await session.flush()
    session.add(InstallationMember(installation_id=inst.id, user_id=user.id, role="owner"))
    await session.commit()

    session.add(InstallationMember(installation_id=inst.id, user_id=user.id, role="member"))
    with pytest.raises(IntegrityError, match="uq_installation_members_pair"):
        await session.commit()


async def test_repository_access_pair_is_unique(session: AsyncSession) -> None:
    user, inst = _user(), _installation()
    session.add_all([user, inst])
    await session.flush()
    repo = _repo(inst)
    session.add(repo)
    await session.flush()
    session.add(RepositoryAccess(user_id=user.id, repository_id=repo.id))
    await session.commit()

    session.add(RepositoryAccess(user_id=user.id, repository_id=repo.id))
    with pytest.raises(IntegrityError, match="uq_repository_access_pair"):
        await session.commit()


async def test_deleting_a_repository_removes_its_access_rows(session: AsyncSession) -> None:
    user, inst = _user(), _installation()
    session.add_all([user, inst])
    await session.flush()
    repo = _repo(inst)
    session.add(repo)
    await session.flush()
    session.add(RepositoryAccess(user_id=user.id, repository_id=repo.id))
    await session.commit()

    await session.delete(repo)
    await session.commit()

    assert (await session.scalars(select(RepositoryAccess))).all() == []


async def test_github_ids_are_unique(session: AsyncSession) -> None:
    session.add(_user(7))
    await session.commit()

    session.add(_user(7))
    with pytest.raises(IntegrityError, match="uq_users_github_id"):
        await session.commit()


async def test_pull_request_number_is_unique_per_repository(session: AsyncSession) -> None:
    inst = _installation()
    session.add(inst)
    await session.flush()
    repo = _repo(inst)
    session.add(repo)
    await session.flush()
    session.add(PullRequest(repository_id=repo.id, number=5, title="a", author_login="x"))
    await session.commit()

    session.add(PullRequest(repository_id=repo.id, number=5, title="b", author_login="y"))
    with pytest.raises(IntegrityError, match="uq_pull_requests_repo_number"):
        await session.commit()


async def test_check_constraint_rejects_unknown_status(session: AsyncSession) -> None:
    session.add(Installation(github_installation_id=1, account_login="a", account_type="alien"))
    with pytest.raises(IntegrityError, match="ck_installations_account_type"):
        await session.commit()


async def test_embedding_must_have_the_configured_dimension(session: AsyncSession) -> None:
    ids = await _seed_chain(session)
    repo = ids["repo"]
    assert isinstance(repo, Repository)

    session.add(
        CodeChunk(
            repository_id=repo.id,
            path="b.py",
            start_line=1,
            end_line=1,
            content="x",
            embedding=[0.1] * (EMBEDDING_DIM - 1),
            commit_sha="b" * 40,
        )
    )
    with pytest.raises(Exception, match="dimensions"):
        await session.commit()


async def test_tsv_is_generated_and_searchable(session: AsyncSession) -> None:
    await _seed_chain(session)

    hits = (
        await session.scalars(
            select(CodeChunk.path).where(
                CodeChunk.tsv.op("@@")(text("websearch_to_tsquery('simple', 'foo')"))
            )
        )
    ).all()

    assert hits == ["a.py"]


async def test_hard_deleting_a_repository_cascades_to_everything_below_it(
    session: AsyncSession,
) -> None:
    ids = await _seed_chain(session)
    repo = ids["repo"]
    assert isinstance(repo, Repository)

    await session.execute(text("DELETE FROM repositories WHERE id = :id"), {"id": repo.id})
    await session.commit()

    for table in (
        "pull_requests",
        "review_runs",
        "findings",
        "code_chunks",
        "chat_sessions",
        "chat_messages",
    ):
        assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


async def test_deleting_a_user_keeps_their_chat_sessions(session: AsyncSession) -> None:
    ids = await _seed_chain(session)
    user, chat = ids["user"], ids["chat"]
    assert isinstance(user, User) and isinstance(chat, ChatSession)

    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    await session.commit()

    owner = await session.scalar(
        text("SELECT user_id FROM chat_sessions WHERE id = :id"), {"id": chat.id}
    )
    assert owner is None
    assert await session.scalar(text("SELECT count(*) FROM chat_sessions")) == 1


async def test_uninstall_marker_keeps_installation_and_review_metadata(
    session: AsyncSession,
) -> None:
    """Uninstall = set uninstalled_at; nothing cascades. (The purge job comes later.)"""
    ids = await _seed_chain(session)
    inst = ids["inst"]
    assert isinstance(inst, Installation)

    await session.execute(
        text("UPDATE installations SET uninstalled_at = now() WHERE id = :id"), {"id": inst.id}
    )
    await session.commit()

    assert await session.scalar(text("SELECT count(*) FROM findings")) == 1
    assert await session.scalar(text("SELECT count(*) FROM review_runs")) == 1


async def test_new_rows_get_uuid_ids_and_timestamps(session: AsyncSession) -> None:
    user = _user()
    session.add(user)
    await session.commit()

    assert isinstance(user.id, uuid.UUID)
    assert user.created_at is not None and user.updated_at is not None
