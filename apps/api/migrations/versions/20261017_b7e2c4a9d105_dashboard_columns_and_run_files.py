"""M6: findings.dismissed, repositories.settings, review_runs.summary, review_run_files

Revision ID: b7e2c4a9d105
Revises: 9d83aec065f4
Create Date: 2026-10-17 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e2c4a9d105"
down_revision: str | None = "9d83aec065f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("dismissed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "repositories",
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "review_runs",
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "review_run_files",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("review_run_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("old_path", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("additions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deletions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("patch", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["review_run_id"],
            ["review_runs.id"],
            name=op.f("fk_review_run_files_review_run_id_review_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_run_files")),
        sa.UniqueConstraint("review_run_id", "path", name="uq_review_run_files_run_path"),
    )
    op.create_index(
        op.f("ix_review_run_files_review_run_id"), "review_run_files", ["review_run_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_review_run_files_review_run_id"), table_name="review_run_files")
    op.drop_table("review_run_files")
    op.drop_column("review_runs", "summary")
    op.drop_column("repositories", "settings")
    op.drop_column("findings", "dismissed")
