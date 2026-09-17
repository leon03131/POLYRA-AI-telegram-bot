"""memories (long-term user memory)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-18

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
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
        "memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_chat_id", sa.Uuid(), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_memories_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memories"),
    )
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_index("ix_memories_normalized_text", "memories", ["normalized_text"])


def downgrade() -> None:
    op.drop_table("memories")
