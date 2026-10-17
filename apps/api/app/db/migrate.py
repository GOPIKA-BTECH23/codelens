"""Run Alembic migrations at deploy time, safely when several instances start at once.

Alembic itself takes no lock on PostgreSQL (checked in alembic 1.20.0: its source has no
advisory-lock call, and its docs do not cover concurrent runs). Two instances running
`upgrade head` together could both try to apply the same revision. So we take a Postgres
*session-level advisory lock* on a dedicated connection first; the second instance waits at
the lock, and when it gets it, `upgrade head` finds nothing left to do.

The lock connection is separate from the one Alembic migrates on. Taking the lock on
Alembic's own connection would start a transaction Alembic did not expect and the DDL would
never commit. A session lock also vanishes by itself if this process dies, so a crash cannot
leave the deploy stuck.

Run it as a command before the server starts: `python -m app.db.migrate`.
"""

import asyncio
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger("app.migrate")

# Arbitrary constant shared by every CodeLens instance ("CODELENS" as a number). Advisory
# locks are keyed by a bigint, and nothing else in our database uses this one.
MIGRATION_LOCK_KEY = 0x434F44454C454E53

_API_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url: str) -> Config:
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


async def migrate(url: str, revision: str = "head") -> None:
    """Upgrade the database at `url` to `revision`, holding the advisory lock meanwhile."""
    # AUTOCOMMIT: the lock connection runs no transaction, it only holds the session lock.
    lock_engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with lock_engine.connect() as lock_conn:
            logger.info("waiting for the migration lock")
            await lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            try:
                logger.info("migration lock acquired, upgrading to %s", revision)
                # env.py calls asyncio.run(), which cannot nest in this loop: use a thread.
                await asyncio.to_thread(command.upgrade, _alembic_config(url), revision)
            finally:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY}
                )
    finally:
        await lock_engine.dispose()
    logger.info("migrations done")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(migrate(settings.database_url.get_secret_value()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
