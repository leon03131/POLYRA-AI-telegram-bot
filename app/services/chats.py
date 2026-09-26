"""Сервисный слой чатов: текущий чат пользователя и запись входящих сообщений."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import ChatRepository, MessageRepository, UserSettingsRepository

if TYPE_CHECKING:
    from app.db.models import Chat, Message


class ChatService:
    """Операции над чатами поверх репозиториев. Commit/rollback — уровень middleware/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._chats = ChatRepository(session)
        self._messages = MessageRepository(session)
        self._settings = UserSettingsRepository(session)

    async def create_chat(self, owner_user_id: uuid.UUID) -> Chat:
        """Создать чат и сразу сделать его текущим для пользователя."""
        chat = await self._chats.create(owner_user_id)
        await self.set_current_chat(owner_user_id, chat.id)
        return chat

    async def list_chats(self, owner_user_id: uuid.UUID) -> list[Chat]:
        """Активные (не архивные) чаты пользователя."""
        return await self._chats.list_for_user(owner_user_id)

    async def get_current_chat_id(self, user_id: uuid.UUID) -> uuid.UUID | None:
        """Текущий чат из UserSettings.extra; None, если не задан/битый."""
        settings = await self._settings.get_or_create(user_id)
        raw = settings.extra.get("current_chat_id")
        if not isinstance(raw, str):
            return None
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None

    async def set_current_chat(self, user_id: uuid.UUID, chat_id: uuid.UUID) -> None:
        """Запомнить текущий чат в UserSettings.extra (merge, без затирания прочих ключей)."""
        settings = await self._settings.get_or_create(user_id)
        await self._settings.update(
            user_id,
            extra={**settings.extra, "current_chat_id": str(chat_id)},
        )

    async def get_or_create_current_chat(self, user_id: uuid.UUID) -> Chat:
        """Текущий чат пользователя; fallback — последний активный; иначе создать новый."""
        current_id = await self.get_current_chat_id(user_id)
        if current_id is not None:
            chat = await self._chats.get(current_id)
            if chat is not None and chat.owner_user_id == user_id and chat.archived_at is None:
                return chat
        latest = await self._chats.get_latest_active(user_id)
        if latest is not None:
            await self.set_current_chat(user_id, latest.id)
            return latest
        return await self.create_chat(user_id)

    async def get_chat_for_user(self, user_id: uuid.UUID, chat_id: uuid.UUID) -> Chat | None:
        """Чат по id с проверкой владельца (web5: генерация в указанный чат).

        Архивный чат возвращается — открытие архива допустимо (как /open);
        удалённый/чужой → None."""
        chat = await self._chats.get(chat_id)
        if chat is None or chat.owner_user_id != user_id:
            return None
        return chat

    async def rename_chat(self, chat_id: uuid.UUID, title: str) -> Chat | None:
        """Переименовать чат; None, если чат не найден."""
        return await self._chats.rename(chat_id, title)

    async def archive_chat(self, chat_id: uuid.UUID) -> Chat | None:
        """Перенести чат в архив; None, если чат не найден."""
        return await self._chats.set_archived(chat_id, True)

    async def delete_chat(self, chat_id: uuid.UUID) -> bool:
        """Удалить чат; True, если запись существовала."""
        return await self._chats.delete(chat_id)

    async def save_user_text_message(self, chat_id: uuid.UUID, text: str) -> Message:
        """Сохранить входящее текстовое сообщение и обновить updated_at чата."""
        # position проставляет add_message (индекс в списке parts)
        message = await self._messages.add_message(
            chat_id,
            "user",
            parts=[{"type": "text", "text": text}],
        )
        await self._chats.touch(chat_id)
        return message

    async def save_user_photo_message(
        self,
        chat_id: uuid.UUID,
        *,
        caption: str | None,
        photo_file_id: str,
        photo_file_size: int | None,
        width: int | None,
        height: int | None,
    ) -> Message:
        """Сохранить входящее фото (+caption) и обновить updated_at чата."""
        # position проставляет add_message (индекс в списке parts)
        parts: list[dict[str, Any]] = [
            {
                "type": "image",
                "telegram_file_id": photo_file_id,
                "mime_type": "image/jpeg",
                "metadata_json": {
                    "file_size": photo_file_size,
                    "width": width,
                    "height": height,
                },
            }
        ]
        if caption:
            parts.append({"type": "text", "text": caption})
        message = await self._messages.add_message(chat_id, "user", parts=parts)
        await self._chats.touch(chat_id)
        return message
