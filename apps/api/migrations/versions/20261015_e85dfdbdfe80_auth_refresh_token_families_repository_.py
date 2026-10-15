"""auth: refresh token families, repository_access, rate limit counters

Revision ID: e85dfdbdfe80
Revises: 80cbc1009c2f
Create Date: 2026-10-15 23:49:31.601719
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e85dfdbdfe80"
down_revision: str | None = "80cbc1009c2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_counters",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_rate_limit_counters")),
    )
    op.create_index("ix_rate_limit_counters_window_start", "rate_limit_counters", ["window_start"])

    # CLAUDE.md decision 13: created together with the login sync that fills it and the
    # dependency that enforces it (app/services/auth.py, app/services/rbac.py).
    op.create_table(
        "repository_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_repository_access_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_repository_access_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repository_access")),
        sa.UniqueConstraint("user_id", "repository_id", name="uq_repository_access_pair"),
    )
    op.create_index("ix_repository_access_repository_id", "repository_access", ["repository_id"])

    # Hand edit: a NOT NULL column cannot be added to a table that already has rows unless
    # it gets a value. Existing tokens each become their own one-token family, then the
    # default is dropped so application code must always choose the family explicitly.
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "family_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.alter_column("refresh_tokens", "family_id", server_default=None)
    op.add_column(
        "refresh_tokens", sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "rotated_at")
    op.drop_column("refresh_tokens", "family_id")
    op.drop_index("ix_repository_access_repository_id", table_name="repository_access")
    op.drop_table("repository_access")
    op.drop_index("ix_rate_limit_counters_window_start", table_name="rate_limit_counters")
    op.drop_table("rate_limit_counters")
