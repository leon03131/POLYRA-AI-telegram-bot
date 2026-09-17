"""Модели сообщения и его частей."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Message(TimestampMixin, Base):
    """Сообщение в чате."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("chats.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system | tool
    # status: pending | streaming | done | cancelled | failed
    status: Mapped[str] = mapped_column(String(16), default="done")
    provider: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(64))
    # без ForeignKey: generation_runs заполняется независимо
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)

    parts: Mapped[list["MessagePart"]] = relationship(
        back_populates="message",
        order_by="MessagePart.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MessagePart(TimestampMixin, Base):
    """Часть сообщения (текст, изображение, tool call/result)."""

    __tablename__ = "message_parts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[str] = mapped_column(String(16))  # text | image | tool_call | tool_result
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str | None] = mapped_column(Text)
    telegram_file_id: Mapped[str | None] = mapped_column(String(256))
    mime_type: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )

    message: Mapped[Message] = relationship(back_populates="parts")
