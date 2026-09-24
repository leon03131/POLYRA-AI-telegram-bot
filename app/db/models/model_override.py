"""Модель DB-override включённости LLM-моделей (admin Models UI)."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ModelOverride(TimestampMixin, Base):
    """Переопределение enabled для модели из кодового реестра (model_id PK).

    Отсутствие записи = дефолт реестра (ModelDefinition.enabled).
    """

    __tablename__ = "model_overrides"

    model_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
