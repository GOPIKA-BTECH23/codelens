"""Seed the LOCAL development database with clearly fake data.

Run from apps/api:   uv run python -m scripts.seed

Safety: refuses to run unless ENVIRONMENT=development AND the database host is localhost.
(A name match on the database is not enough: a production URL can contain "codelens".)
Everything it creates is labelled fake: negative GitHub ids (real ones are always positive),
logins/names starting with "fake-", and "[FAKE]" in finding text. Running it twice is a no-op.
"""

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import (
    Finding,
    Installation,
    InstallationMember,
    PullRequest,
    Repository,
    ReviewRun,
    User,
)
from app.db.session import create_engine, create_session_factory

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
FAKE_INSTALLATION_ID = -1


class UnsafeSeedTarget(Exception):
    """Raised when the seed script is pointed at something that is not a local dev database."""


def assert_safe_target(settings: Settings) -> None:
    """Raise UnsafeSeedTarget unless this is ENVIRONMENT=development on a localhost database."""
    if settings.environment != "development":
        raise UnsafeSeedTarget(
            f"ENVIRONMENT is {settings.environment!r}; the seed script needs 'development'."
        )
    host = make_url(settings.database_url.get_secret_value()).host
    if host not in LOCAL_HOSTS:
        raise UnsafeSeedTarget(f"database host {host!r} is not localhost; refusing to seed.")


async def seed(session: AsyncSession) -> bool:
    """Insert the fake dataset. Returns False if it was already there."""
    exists = await session.scalar(
        select(Installation.id).where(Installation.github_installation_id == FAKE_INSTALLATION_ID)
    )
    if exists:
        return False

    owner = User(github_id=-1, login="fake-owner", name="Fake Owner")
    member = User(github_id=-2, login="fake-member", name="Fake Member")
    inst = Installation(
        github_installation_id=FAKE_INSTALLATION_ID,
        account_login="fake-org",
        account_type="organization",
    )
    session.add_all([owner, member, inst])
    await session.flush()
    session.add_all(
        [
            InstallationMember(installation_id=inst.id, user_id=owner.id, role="admin"),
            InstallationMember(installation_id=inst.id, user_id=member.id, role="member"),
        ]
    )

    for n, name in enumerate(["fake-org/fake-api", "fake-org/fake-web"], start=1):
        repo = Repository(installation_id=inst.id, github_repo_id=-n, full_name=name)
        session.add(repo)
        await session.flush()
        pr = PullRequest(
            repository_id=repo.id,
            number=1,
            title="[FAKE] Example pull request",
            author_login="fake-member",
        )
        session.add(pr)
        await session.flush()
        run = ReviewRun(
            pull_request_id=pr.id, status="succeeded", model="fake-llm", prompt_version="seed"
        )
        session.add(run)
        await session.flush()
        session.add_all(
            Finding(
                review_run_id=run.id,
                file="fake/example.py",
                line=line,
                severity=severity,
                category=category,
                message=f"[FAKE] {category} finding for local development only.",
                suggestion="[FAKE] No real suggestion.",
                confidence=0.8,
            )
            for line, severity, category in [(3, "high", "security"), (10, "low", "style")]
        )
    return True


async def main() -> int:
    settings = get_settings()
    try:
        assert_safe_target(settings)
    except UnsafeSeedTarget as exc:
        print(f"Refusing to seed: {exc}", file=sys.stderr)
        return 1

    engine = create_engine(settings)
    try:
        async with create_session_factory(engine).begin() as session:
            created = await seed(session)
    finally:
        await engine.dispose()
    print("Seeded fake development data." if created else "Already seeded; nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
