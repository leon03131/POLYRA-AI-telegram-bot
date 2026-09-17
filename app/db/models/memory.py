"""Модель долговременной памяти пользователя."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Memory(TimestampMixin, Base):
    """Долговременный факт о пользователе (стабильные предпочтения, проекты и т.п.).

    normalized_text — нормализованная форма для дедупликации.
    embedding — placeholder под pgvector (M7 embeddings не реализует).
    """

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text, index=True)
    category: Mapped[str] = mapped_column(String(32), default="general")
    importance: Mapped[int] = mapped_column(Integer, default=5)  # 1..10
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_chat_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    embedding: Mapped[list[float] | None] = mapped_column(JSONB)
