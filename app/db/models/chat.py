"""Модель чата."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Chat(TimestampMixin, Base):
    """Чат пользователя. NULL в настройках = наследовать дефолты пользователя."""

    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(256))
    model_id: Mapped[str | None] = mapped_column(String(64))
    thinking_setting: Mapped[str | None] = mapped_column(String(16))
    web_mode: Mapped[str | None] = mapped_column(String(8))  # off | auto | on
    memory_enabled: Mapped[bool | None] = mapped_column(Boolean)
    system_prompt_override: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
