from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Finding, User
from scripts.seed import seed


async def test_seed_creates_labelled_fake_data_and_is_idempotent(session: AsyncSession) -> None:
    assert await seed(session) is True
    await session.commit()
    assert await seed(session) is False  # second run is a no-op

    assert await session.scalar(select(func.count()).select_from(User)) == 2
    assert await session.scalar(select(func.min(User.github_id))) < 0  # fake ids are negative
    messages = (await session.scalars(select(Finding.message))).all()
    assert messages and all(m and m.startswith("[FAKE]") for m in messages)
