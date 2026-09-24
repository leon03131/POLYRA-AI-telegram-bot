"""Доступ к API-ключам сторонних провайдеров: БД (enabled) → env fallback."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import ProviderCredentialRepository
from app.security.crypto import CryptoBox


async def get_provider_api_key(
    session: AsyncSession,
    crypto: CryptoBox,
    provider: str,
    env_fallback: str = "",
) -> str | None:
    """Расшифрованный ключ провайдера; None, если нигде не настроен.

    Приоритет: запись provider_credentials → непустой env_fallback (bootstrap),
    причём env_fallback применяется ТОЛЬКО при отсутствии записи. Запись с
    enabled=False — абсолютный запрет: env НЕ подставляется (A14, контракт §10).
    """
    credential = await ProviderCredentialRepository(session).get(provider)
    if credential is not None:
        if not credential.enabled:
            return None
        return crypto.decrypt(credential.encrypted_api_key)
    return env_fallback or None
