"""Модель аудита вызовов инструментов."""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ToolCallRecord(TimestampMixin, Base):
    """Запись о вызове инструмента в рамках генерации."""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    generation_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        index=True,
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("chats.id", ondelete="CASCADE"),
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[str] = mapped_column(Text, default="")
    # status: ok | error | timeout | denied | invalid_args
    status: Mapped[str] = mapped_column(String(16), default="ok")
    result_preview: Mapped[str] = mapped_column(String(500), default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
