"""fix v2: model access mode, durable usage ledger, owner cleanup, overrides, FTS

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

POLYRA FIX V2 (контракт .agents/POLYRA_FIX_V2_CONTRACTS.md §2/§3/§10):

1. user_model_access — режим доступа к моделям ('all'|'list'); backfill:
   пользователи со строками в user_model_permissions → mode='list'. Пустой
   allowlist отныне отличим от unrestricted (A03).
2. generation_runs.chat_id → NULL + FK ON DELETE SET NULL: удаление чата не
   стирает usage-ledger (A08).
3. Partial unique index uq_generation_runs_active_chat (chat_id) WHERE status IN
   ('queued','running') — одна активная генерация на чат (A05).
4. UPDATE users SET is_owner=false WHERE telegram_user_id <> 795063564 — owner
   identity только numeric telegram_user_id, флаг прав не даёт (A04).
5. model_overrides — DB-override enabled для моделей кодового реестра (A28).
6. GIN FTS-индекс ix_memories_text_fts ON memories (to_tsvector('simple', text)).
7. generation_runs.attempts JSONB — имена проектов по попыткам пула (§2).

Работает и на пустой, и на существующей БД (data-операции на пустых таблицах —
no-op). Предположение для п.3: в живой БД не более одного active
(queued/running) run на чат — иначе CREATE UNIQUE INDEX потребует ручного
дедупа (зафиксировано в отчёте wave1).

Downgrade зеркален, КРОМЕ: backfill п.1 и очистка is_owner п.4 НЕ откатываются
(п.1 самоустраняется drop таблицы; повторный upgrade перевосстановит mode='list'
по текущему содержимому user_model_permissions). Откат NOT NULL chat_id
удаляет строки generation_runs с chat_id IS NULL — они появляются только при
удалении чатов на схеме 0009 (orphaned ledger); иначе NOT NULL восстановить
невозможно.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Owner identity — numeric Telegram id владельца (контракт §3 / config default).
_OWNER_TELEGRAM_ID = 795063564


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
    # 1. user_model_access + backfill из user_model_permissions (A03) ----------
    op.create_table(
        "user_model_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=8), server_default="all", nullable=False),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_model_access_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_model_access"),
    )
    # Пользователи с существующими строками разрешений — это режим 'list'
    # (до миграции пустой результат allowed_model_ids был неотличим от
    # unrestricted; явных «запретить всех» в прошлом зафиксировать нельзя).
    op.execute(
        "INSERT INTO user_model_access (user_id, mode) "
        "SELECT DISTINCT user_id, 'list' FROM user_model_permissions "
        "ON CONFLICT (user_id) DO NOTHING"
    )

    # 2. generation_runs.chat_id → NULL + ON DELETE SET NULL (A08) -------------
    op.drop_constraint("fk_generation_runs_chat_id_chats", "generation_runs", type_="foreignkey")
    op.alter_column("generation_runs", "chat_id", existing_type=sa.Uuid(), nullable=True)
    op.create_foreign_key(
        "fk_generation_runs_chat_id_chats",
        "generation_runs",
        "chats",
        ["chat_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Одна активная генерация на чат (A05) ----------------------------------
    op.create_index(
        "uq_generation_runs_active_chat",
        "generation_runs",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    # 4. Owner identity: только numeric id; чужие флаги is_owner снимаем (A04) -
    op.execute(f"UPDATE users SET is_owner = false WHERE telegram_user_id <> {_OWNER_TELEGRAM_ID}")

    # 5. model_overrides (A28) --------------------------------------------------
    op.create_table(
        "model_overrides",
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint("model_id", name="pk_model_overrides"),
    )

    # 6. GIN FTS-индекс для memories.text (config simple) ----------------------
    op.create_index(
        "ix_memories_text_fts",
        "memories",
        [sa.text("to_tsvector('simple', text)")],
        postgresql_using="gin",
    )

    # 7. generation_runs.attempts JSONB (контракт §2) ---------------------------
    op.add_column(
        "generation_runs",
        sa.Column("attempts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    # 7.
    op.drop_column("generation_runs", "attempts")
    # 6.
    op.drop_index("ix_memories_text_fts", table_name="memories")
    # 5.
    op.drop_table("model_overrides")
    # 4. Очистка is_owner НЕ откатывается (см. docstring).
    # 3.
    op.drop_index("uq_generation_runs_active_chat", table_name="generation_runs")
    # 2. Строки с chat_id IS NULL (orphaned ledger после удаления чатов на 0009)
    #    несовместимы с NOT NULL — удаляем при откате (документировано).
    op.drop_constraint("fk_generation_runs_chat_id_chats", "generation_runs", type_="foreignkey")
    op.execute("DELETE FROM generation_runs WHERE chat_id IS NULL")
    op.alter_column("generation_runs", "chat_id", existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key(
        "fk_generation_runs_chat_id_chats",
        "generation_runs",
        "chats",
        ["chat_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # 1. Backfill не откатывается (см. docstring); таблица удаляется целиком.
    op.drop_table("user_model_access")
