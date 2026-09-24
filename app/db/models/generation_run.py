"""Модель запуска генерации ответа."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class GenerationRun(TimestampMixin, Base):
    """Один запуск генерации: провайдер, модель, статус, usage, ошибка.

    chat_id nullable + ON DELETE SET NULL: удаление чата НЕ стирает
    usage-ledger (A08). Partial unique index на активные статусы — одна
    активная генерация на чат (A05). attempts — имена Gemini-проектов
    по попыткам пула (последний = gemini_project_id, контракт FIX V2 §2).
    """

    __tablename__ = "generation_runs"
    __table_args__ = (
        Index(
            "uq_generation_runs_active_chat",
            "chat_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("chats.id", ondelete="SET NULL"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    provider: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(64))
    thinking_setting: Mapped[str | None] = mapped_column(String(16))
    # status: queued | running | completed | cancelled | failed | aborted
    status: Mapped[str] = mapped_column(String(16), default="queued")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    first_token_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    tool_calls_count: Mapped[int] = mapped_column(Integer, default=0)
    gemini_project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    attempts: Mapped[list[str] | None] = mapped_column(JSONB)  # имена проектов-попыток
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    draft_id: Mapped[int | None] = mapped_column(BigInteger)  # корреляция с Telegram draft
