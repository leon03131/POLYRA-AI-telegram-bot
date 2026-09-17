"""gemini pool: gemini_projects, quota_policies, quota_minute_usage, quota_daily_usage

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
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
        "gemini_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=16), nullable=False),
        sa.Column("rotation_order", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("health_status", sa.String(length=16), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=256), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_gemini_projects"),
        sa.UniqueConstraint("name", name="uq_gemini_projects_name"),
    )

    op.create_table(
        "quota_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("rpm", sa.Integer(), nullable=True),
        sa.Column("tpm", sa.Integer(), nullable=True),
        sa.Column("rpd", sa.Integer(), nullable=True),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_quota_policies"),
        sa.UniqueConstraint("model_id", name="uq_quota_policies_model_id"),
    )

    op.create_table(
        "quota_minute_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("minute_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests_count", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["gemini_projects.id"],
            name="fk_quota_minute_usage_project_id_gemini_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quota_minute_usage"),
        sa.UniqueConstraint(
            "project_id",
            "model_id",
            "minute_ts",
            name="uq_quota_minute_usage_project_id",
        ),
    )
    op.create_index("ix_quota_minute_usage_minute_ts", "quota_minute_usage", ["minute_ts"])

    op.create_table(
        "quota_daily_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests_count", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["gemini_projects.id"],
            name="fk_quota_daily_usage_project_id_gemini_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quota_daily_usage"),
        sa.UniqueConstraint(
            "project_id",
            "model_id",
            "day",
            name="uq_quota_daily_usage_project_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("quota_daily_usage")
    op.drop_index("ix_quota_minute_usage_minute_ts", table_name="quota_minute_usage")
    op.drop_table("quota_minute_usage")
    op.drop_table("quota_policies")
    op.drop_table("gemini_projects")
