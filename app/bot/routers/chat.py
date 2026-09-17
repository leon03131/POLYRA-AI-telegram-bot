"""Приём текстовых сообщений: запуск генерации ответа модели."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Bot, F, Router
from aiogram.types import Message

from app.db.models import User
from app.services.access import EffectivePermissions

if TYPE_CHECKING:
    from app.services.generation import GenerationService

router = Router(name="chat")


@router.message(F.text, ~F.text.startswith("/"))
async def on_text_message(
    message: Message,
    user: User,
    permissions: EffectivePermissions,
    bot: Bot,
    generation_service: GenerationService,
) -> None:
    """Передать текст пользователя в GenerationService.

    Хендлер await'ит генерацию напрямую: aiogram 3 запускает хендлеры
    как конкурентные задачи, поэтому stop-апдейты обрабатываются параллельно.
    Персист истории, стриминг и лимиты — внутри GenerationService.
    """
    if not message.text:
        return
    await generation_service.generate(
        bot=bot,
        tg_chat_id=message.chat.id,
        user=user,
        permissions=permissions,
        current_parts=[{"type": "text", "text": message.text}],
    )
