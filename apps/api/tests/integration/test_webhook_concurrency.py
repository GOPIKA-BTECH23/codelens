"""Two deliveries processed at the same time, in two real transactions.

These pin the concurrency claims in app/services/webhooks.py: the PR upsert's row lock
serialises webhooks for one PR, and the unique delivery id serialises copies of one
delivery. Without them, both transactions could leave a run `queued`.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ReviewRun, WebhookDelivery
from app.services.jobs import PostgresJobQueue
from app.services.webhooks import WebhookPolicy, process_delivery

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "webhooks"
POLICY = WebhookPolicy(ignored_pr_authors=frozenset())


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


async def test_second_webhook_for_a_pr_waits_and_supersedes_the_first(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    queue = PostgresJobQueue(session_factory)
    async with session_factory() as first, session_factory() as second:
        await process_delivery(
            first,
            queue,
            POLICY,
            delivery_id="d-opened",
            event="pull_request",
            payload=fixture("pull_request.opened"),
        )  # not committed yet: holds the PR row lock

        racing = asyncio.create_task(
            process_delivery(
                second,
                queue,
                POLICY,
                delivery_id="d-sync",
                event="pull_request",
                payload=fixture("pull_request.synchronize"),
            )
        )
        await asyncio.sleep(0.3)
        assert not racing.done()  # blocked on the PR row, not racing ahead

        await first.commit()
        await racing
        await second.commit()

    statuses = (
        await session.scalars(select(ReviewRun.status).order_by(ReviewRun.created_at))
    ).all()
    assert list(statuses) == ["superseded", "queued"]


async def test_concurrent_copies_of_one_delivery_are_processed_once(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    queue = PostgresJobQueue(session_factory)
    payload = fixture("pull_request.opened")
    async with session_factory() as first, session_factory() as second:
        result_1 = await process_delivery(
            first, queue, POLICY, delivery_id="d-same", event="pull_request", payload=payload
        )
        racing = asyncio.create_task(
            process_delivery(
                second, queue, POLICY, delivery_id="d-same", event="pull_request", payload=payload
            )
        )
        await asyncio.sleep(0.3)
        assert not racing.done()  # waiting on the unique index

        await first.commit()
        result_2 = await racing
        await second.commit()

    assert (result_1.status, result_2.status) == ("queued", "duplicate")
    assert await session.scalar(select(func.count()).select_from(WebhookDelivery)) == 1
    assert await session.scalar(select(func.count()).select_from(ReviewRun)) == 1
