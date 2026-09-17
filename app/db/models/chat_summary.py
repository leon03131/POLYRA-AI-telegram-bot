"""Модель сводки чата (compaction state)."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, Uuid
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChatSummary(TimestampMixin, Base):
    """JSON-сводка сжатого префикса истории чата.

    Raw history в PostgreSQL НЕ удаляется: запись лишь помечает покрытый
    префикс (covered_until_message_id / covered_messages_count). Одна запись
    на чат (upsert по chat_id).
    """

    __tablename__ = "chat_summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("chats.id", ondelete="CASCADE"),
        unique=True,
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )
    covered_until_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    covered_messages_count: Mapped[int] = mapped_column(Integer, default=0)
