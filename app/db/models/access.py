"""Модели доступа: гранты, per-user разрешения моделей, настройки."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AccessGrant(TimestampMixin, Base):
    """Грант доступа пользователя. Одна активная запись на пользователя."""

    __tablename__ = "access_grants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|suspended|revoked
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # NULL = permanent
    requests_per_day: Mapped[int | None] = mapped_column(Integer)
    token_limit: Mapped[int | None] = mapped_column(BigInteger)
    max_concurrent_generations: Mapped[int] = mapped_column(Integer, default=1)
    can_use_web_search: Mapped[bool] = mapped_column(Boolean, default=True)
    can_use_memory: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(BigInteger)  # telegram id owner'а
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserModelPermission(TimestampMixin, Base):
    """Per-user разрешение/запрет конкретной модели."""

    __tablename__ = "user_model_permissions"
    __table_args__ = (UniqueConstraint("user_id", "model_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(64))
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)


class UserSettings(TimestampMixin, Base):
    """Персональные настройки пользователя (1:1 с users)."""

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    default_model_id: Mapped[str | None] = mapped_column(String(64))
    default_thinking: Mapped[str | None] = mapped_column(String(16))
    web_mode: Mapped[str] = mapped_column(String(8), default="auto")  # off | auto | on
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    locale: Mapped[str | None] = mapped_column(String(8))
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
