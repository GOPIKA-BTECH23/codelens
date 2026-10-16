from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Finding, RepositoryAccess, ReviewRunFile, User
from scripts.seed import seed


async def test_seed_creates_labelled_fake_data_and_is_idempotent(session: AsyncSession) -> None:
    assert await seed(session) is True
    await session.commit()
    assert await seed(session) is False  # second run is a no-op

    assert await session.scalar(select(func.count()).select_from(User)) == 2
    assert await session.scalar(select(func.min(User.github_id))) < 0  # fake ids are negative
    messages = (await session.scalars(select(Finding.message))).all()
    assert messages and all(m and m.startswith("[FAKE]") for m in messages)


async def test_seeded_findings_point_at_lines_of_the_seeded_diff(session: AsyncSession) -> None:
    from app.services.diff import parse_hunks

    await seed(session)
    await session.commit()

    patch = await session.scalar(select(ReviewRunFile.patch))
    shown = {ln.new_line for h in parse_hunks(patch or "") for ln in h.lines}
    assert set(await session.scalars(select(Finding.line))) <= shown
    # both fake users may see both fake repos
    assert await session.scalar(select(func.count()).select_from(RepositoryAccess)) == 4
