"""Приём фото: скачивание, сборка parts (image первым), запуск генерации."""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from app.config import Settings
from app.db.models import User
from app.services.access import EffectivePermissions

if TYPE_CHECKING:
    from app.services.generation import GenerationService

logger = logging.getLogger(__name__)

router = Router(name="photos")

_DOWNLOAD_ERROR_TEXT = "⚠️ Не удалось загрузить фото. Попробуйте ещё раз."


@router.message(F.photo)
async def on_photo_message(
    message: Message,
    user: User,
    permissions: EffectivePermissions,
    bot: Bot,
    generation_service: GenerationService,
    settings: Settings,
) -> None:
    """Скачать фото, собрать parts (image, затем caption — как в M2), запустить генерацию."""
    if not message.photo:
        return
    max_bytes = settings.photo_max_bytes
    photo = message.photo[-1]  # максимальное разрешение из вариантов
    if photo.file_size is not None and photo.file_size > max_bytes:
        await message.answer("⚠️ Фото слишком большое.")
        return
    try:
        file = await bot.get_file(photo.file_id)
        if file.file_path is None:
            await message.answer(_DOWNLOAD_ERROR_TEXT)
            return
        buf = await bot.download_file(file.file_path)
        data = buf.read() if buf is not None else b""
    except TelegramAPIError:
        logger.info("photo download failed chat=%s", message.chat.id, exc_info=True)
        await message.answer(_DOWNLOAD_ERROR_TEXT)
        return
    # file_size может отсутствовать — проверяем фактический размер после скачивания.
    if not data or len(data) > max_bytes:
        await message.answer(_DOWNLOAD_ERROR_TEXT if not data else "⚠️ Фото слишком большое.")
        return
    parts: list[dict[str, Any]] = [
        {
            "type": "image",
            # A18: устойчивый file_id — bytes в БД не храним (data_base64
            # отбрасывается репозиторием), rehydration по требованию.
            "telegram_file_id": photo.file_id,
            "mime_type": "image/jpeg",
            "data_base64": base64.b64encode(data).decode("ascii"),
            "metadata_json": {
                "file_unique_id": photo.file_unique_id,
                "width": photo.width,
                "height": photo.height,
                "file_size": photo.file_size,
            },
        }
    ]
    if message.caption:
        parts.append({"type": "text", "text": message.caption})
    await generation_service.generate(
        bot=bot,
        tg_chat_id=message.chat.id,
        user=user,
        permissions=permissions,
        current_parts=parts,
    )
