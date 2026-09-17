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

    Приоритет: enabled-запись в provider_credentials (decrypt) → непустой
    env_fallback (bootstrap из настроек).
    """
    credential = await ProviderCredentialRepository(session).get(provider)
    if credential is not None and credential.enabled:
        return crypto.decrypt(credential.encrypted_api_key)
    return env_fallback or None
