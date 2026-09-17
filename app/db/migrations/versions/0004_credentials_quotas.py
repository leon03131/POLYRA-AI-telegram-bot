"""provider credentials + начальные quota policies для Gemini-моделей

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Начальные лимиты (редактируются в админке): (model_id, rpm, tpm, rpd).
_SEEDED_QUOTA_POLICIES: tuple[tuple[str, int, int, int], ...] = (
    ("gemini-3.8-flash", 4, 249999, 19),
    ("gemini-3.7-flash", 4, 249999, 19),
    ("gemini-3.6-flash", 4, 249999, 19),
)


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


def _seed_quota_policies() -> None:
    """Вставить начальные лимиты квот; повторный запуск не падает (ON CONFLICT)."""
    quota_policies = sa.table(
        "quota_policies",
        sa.column("id", sa.Uuid()),
        sa.column("model_id", sa.String()),
        sa.column("rpm", sa.Integer()),
        sa.column("tpm", sa.Integer()),
        sa.column("rpd", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "model_id": model_id,
            "rpm": rpm,
            "tpm": tpm,
            "rpd": rpd,
            "created_at": sa.func.now(),
            "updated_at": sa.func.now(),
        }
        for model_id, rpm, tpm, rpd in _SEEDED_QUOTA_POLICIES
    ]
    stmt = postgresql.insert(quota_policies).values(rows)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_quota_policies_model_id")
    op.execute(stmt)


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_provider_credentials"),
        sa.UniqueConstraint("provider", name="uq_provider_credentials_provider"),
    )
    _seed_quota_policies()


def downgrade() -> None:
    op.drop_table("provider_credentials")
    quota_policies = sa.table("quota_policies", sa.column("model_id", sa.String()))
    op.execute(
        quota_policies.delete().where(
            quota_policies.c.model_id.in_([model_id for model_id, *_ in _SEEDED_QUOTA_POLICIES])
        )
    )
