"""Репозиторий credentials сторонних провайдеров (не-Gemini ключи)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderCredential


class ProviderCredentialRepository:
    """Операции над ProviderCredential. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, provider: str) -> ProviderCredential | None:
        """Найти credential по имени провайдера; None, если не заведён."""
        stmt = select(ProviderCredential).where(ProviderCredential.provider == provider)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self, provider: str, encrypted_api_key: str, key_hint: str
    ) -> ProviderCredential:
        """Создать credential или заменить ключ/подсказку (enabled не трогаем)."""
        credential = await self.get(provider)
        if credential is None:
            credential = ProviderCredential(
                provider=provider,
                encrypted_api_key=encrypted_api_key,
                key_hint=key_hint,
            )
            self._session.add(credential)
        else:
            credential.encrypted_api_key = encrypted_api_key
            credential.key_hint = key_hint
        await self._session.flush()
        return credential

    async def list_all(self) -> list[ProviderCredential]:
        """Все credentials (по имени провайдера)."""
        stmt = select(ProviderCredential).order_by(ProviderCredential.provider)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, provider: str, enabled: bool) -> ProviderCredential | None:
        """Включить/выключить credential; None, если не найден."""
        credential = await self.get(provider)
        if credential is None:
            return None
        credential.enabled = enabled
        await self._session.flush()
        return credential

    async def delete(self, provider: str) -> bool:
        """Удалить credential; True, если запись существовала."""
        credential = await self.get(provider)
        if credential is None:
            return False
        await self._session.delete(credential)
        await self._session.flush()
        return True
