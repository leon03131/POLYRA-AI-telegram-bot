"""Репозиторий пользователей."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class UserRepository:
    """Lookup/upsert операции над User. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Найти пользователя по внутреннему UUID."""
        return await self._session.get(User, user_id)

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        """Найти пользователя по numeric Telegram ID."""
        stmt = select(User).where(User.telegram_user_id == telegram_user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_telegram_user(
        self,
        telegram_user_id: int,
        *,
        username: str | None,
        first_name: str,
        last_name: str | None,
        language_code: str | None,
    ) -> User:
        """Вставить нового или обновить username/names/language_code + last_seen_at."""
        user = await self.get_by_telegram_id(telegram_user_id)
        now = datetime.now(UTC)
        if user is None:
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                last_seen_at=now,
            )
            self._session.add(user)
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.language_code = language_code
            user.last_seen_at = now
        await self._session.flush()
        return user

    async def set_status(self, user_id: uuid.UUID, status: str) -> User | None:
        """Установить status (active|banned); None, если пользователь не найден."""
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.status = status
        await self._session.flush()
        return user

    async def search(self, query: str, limit: int = 20) -> list[User]:
        """Поиск: цифровой query — точный telegram_user_id ИЛИ username ilike; иначе ilike."""
        pattern = f"%{query}%"
        stmt = select(User)
        if query.isdigit():
            stmt = stmt.where(
                or_(
                    User.telegram_user_id == int(query),
                    User.username.ilike(pattern),
                )
            )
        else:
            stmt = stmt.where(User.username.ilike(pattern))
        stmt = stmt.order_by(User.last_seen_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
