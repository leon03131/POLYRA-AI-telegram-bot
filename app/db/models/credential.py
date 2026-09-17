"""Модель credentials сторонних провайдеров (не-Gemini API-ключи)."""

import uuid

from sqlalchemy import Boolean, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ProviderCredential(TimestampMixin, Base):
    """Зашифрованный API-ключ провайдера ("alibaba", "serper", "serpapi", "brave", "jina")."""

    __tablename__ = "provider_credentials"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), unique=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text)  # Fernet token, НЕ plaintext
    key_hint: Mapped[str] = mapped_column(String(16))  # последние 4 символа ключа (UI-маска)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
