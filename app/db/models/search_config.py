"""Модель конфигурации поисковых бэкендов (web_search / open_url)."""

import uuid

from sqlalchemy import Boolean, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SearchBackendConfig(TimestampMixin, Base):
    """Конфиг поискового бэкенда: enabled, priority, зашифрованный ключ, health."""

    __tablename__ = "search_backend_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(32), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)  # Fernet, НЕ plaintext
    key_hint: Mapped[str | None] = mapped_column(String(16))  # UI-маска ключа
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_error: Mapped[str | None] = mapped_column(String(256))
