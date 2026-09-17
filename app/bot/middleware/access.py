"""Middleware доступа: upsert пользователя, проверка гранта, инъекция в data.

Для допущенных пользователей кладёт в handler data: ``user``, ``permissions``,
``db_session`` (одна сессия на update, commit после хендлера, rollback при ошибке).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.repositories import AccessRepository, ModelPermissionRepository, UserRepository
from app.services.access import GrantView, evaluate_access

logger = logging.getLogger(__name__)

_DENY_MESSAGE_TEXT = "⛔ Доступ не активирован. Обратитесь к администратору бота."
_DENY_CALLBACK_TEXT = "Доступ не активирован"


class AccessMiddleware(BaseMiddleware):
    """Единая точка входа: identity + доступ + транзакция БД на update."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)

        async with self._session_factory() as session:
            user_repo = UserRepository(session)
            user = await user_repo.upsert_telegram_user(
                tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
            )
            is_owner = tg_user.id == self._settings.owner_telegram_id
            if is_owner and not user.is_owner:
                user.is_owner = True
                await session.flush()

            grant = await AccessRepository(session).get_grant(user.id)
            grant_view = (
                GrantView(
                    status=grant.status,
                    expires_at=grant.expires_at,
                    requests_per_day=grant.requests_per_day,
                    token_limit=grant.token_limit,
                    max_concurrent_generations=grant.max_concurrent_generations,
                    can_use_web_search=grant.can_use_web_search,
                    can_use_memory=grant.can_use_memory,
                )
                if grant is not None
                else None
            )
            allowed_models = await ModelPermissionRepository(session).allowed_model_ids(user.id)
            permissions = evaluate_access(
                is_owner=is_owner,
                user_status=user.status,
                grant=grant_view,
                allowed_models=allowed_models,
                now=datetime.now(UTC),
            )

            if not permissions.allowed:
                logger.info(
                    "access denied user=%s reason=%s",
                    tg_user.id,
                    permissions.reason,
                )
                if isinstance(event, CallbackQuery):
                    await event.answer(_DENY_CALLBACK_TEXT, show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(_DENY_MESSAGE_TEXT)
                await session.commit()  # зафиксировать upsert пользователя
                return None

            data["user"] = user
            data["permissions"] = permissions
            data["db_session"] = session
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result
