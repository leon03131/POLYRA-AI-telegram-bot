"""Модели пула Gemini-проектов и учёта квот."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class GeminiProject(TimestampMixin, Base):
    """Gemini-проект: зашифрованный API-ключ, порядок ротации, health, cooldown."""

    __tablename__ = "gemini_projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text)  # Fernet token, НЕ plaintext
    key_hint: Mapped[str] = mapped_column(String(16))  # последние 4 символа ключа (UI-маска)
    rotation_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # health_status: unknown | healthy | unhealthy
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(256))


class QuotaPolicy(TimestampMixin, Base):
    """Лимиты квот на модель (одинаковы для всех проектов). NULL = unlimited."""

    __tablename__ = "quota_policies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[str] = mapped_column(String(64), unique=True)
    rpm: Mapped[int | None] = mapped_column(Integer)  # requests/min
    tpm: Mapped[int | None] = mapped_column(Integer)  # input tokens/min
    # requests/day; reset — полночь America/Los_Angeles
    rpd: Mapped[int | None] = mapped_column(Integer)


class QuotaMinuteUsage(Base):
    """Потребление за минуту (minute_ts — UTC, усечён до минуты)."""

    __tablename__ = "quota_minute_usage"
    __table_args__ = (UniqueConstraint("project_id", "model_id", "minute_ts"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("gemini_projects.id", ondelete="CASCADE"),
    )
    model_id: Mapped[str] = mapped_column(String(64))
    minute_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    requests_count: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)


class QuotaDailyUsage(Base):
    """Потребление за день (day — дата в America/Los_Angeles)."""

    __tablename__ = "quota_daily_usage"
    __table_args__ = (UniqueConstraint("project_id", "model_id", "day"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("gemini_projects.id", ondelete="CASCADE"),
    )
    model_id: Mapped[str] = mapped_column(String(64))
    day: Mapped[date] = mapped_column(Date)
    requests_count: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
