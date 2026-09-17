"""search tools: search_backend_configs + tool_calls

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_columns() -> list[sa.Column[datetime]]:
    """Общие колонки TimestampMixin (server_default now())."""
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "search_backend_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("key_hint", sa.String(length=16), nullable=True),
        sa.Column("health_status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.String(length=256), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_search_backend_configs"),
        sa.UniqueConstraint("backend_id", name="uq_search_backend_configs_backend_id"),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments_json", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column("result_preview", sa.String(length=500), server_default="", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            name="fk_tool_calls_generation_run_id_generation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name="fk_tool_calls_chat_id_chats",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
    )
    op.create_index("ix_tool_calls_generation_run_id", "tool_calls", ["generation_run_id"])


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("search_backend_configs")
