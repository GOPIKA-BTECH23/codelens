"""RUN_WORKER_IN_API=true: the API process runs the job loop in its lifespan."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.enums import JobStatus
from app.main import create_app
from app.services.jobs import PostgresJobQueue
from app.services.webhooks import JOB_INDEX_REPO
from tests.app_utils import browser, make_settings
from tests.integration.test_worker import enqueue, job_is, wait_for


async def test_api_process_runs_jobs_when_enabled_and_stops_cleanly(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    settings = make_settings(
        database_url=migrated_db, run_worker_in_api=True, worker_poll_seconds=0.05
    )
    queue = PostgresJobQueue(session_factory)
    job_id = await enqueue(queue, session_factory, JOB_INDEX_REPO, {"repository_id": "r"})

    with browser(create_app(settings)):  # entering runs the lifespan; leaving shuts the worker down
        await wait_for(job_is(session_factory, job_id, JobStatus.SUCCEEDED))


async def test_api_process_leaves_jobs_alone_when_disabled(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    import asyncio

    settings = make_settings(database_url=migrated_db, worker_poll_seconds=0.05)
    queue = PostgresJobQueue(session_factory)
    job_id = await enqueue(queue, session_factory, JOB_INDEX_REPO, {"repository_id": "r"})

    with browser(create_app(settings)):
        await asyncio.sleep(0.5)
        assert await job_is(session_factory, job_id, JobStatus.QUEUED)()
