"""review_runs: allow the 'skipped' status (M4)

Revision ID: a4c1d9e2b7f3
Revises: e85dfdbdfe80
Create Date: 2026-10-17 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a4c1d9e2b7f3"
down_revision: str | None = "e85dfdbdfe80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "status IN ('queued', 'running', 'succeeded', 'failed', 'superseded')"
NEW = "status IN ('queued', 'running', 'succeeded', 'failed', 'superseded', 'skipped')"


# Hand written: Alembic autogenerate does not detect changes to CHECK constraints. The reason
# a run was skipped goes into the existing `error` column, so no new column is needed.
def upgrade() -> None:
    op.drop_constraint(op.f("ck_review_runs_status"), "review_runs", type_="check")
    op.create_check_constraint(op.f("ck_review_runs_status"), "review_runs", NEW)


def downgrade() -> None:
    # Rows already marked 'skipped' would violate the old constraint; treat them as failed.
    op.execute("UPDATE review_runs SET status = 'failed' WHERE status = 'skipped'")
    op.drop_constraint(op.f("ck_review_runs_status"), "review_runs", type_="check")
    op.create_check_constraint(op.f("ck_review_runs_status"), "review_runs", OLD)
