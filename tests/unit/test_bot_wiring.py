"""Тесты wiring генерации в bot-слой (M5). Без Telegram API, БД и сети."""

from __future__ import annotations

import asyncio
import base64
import io
import uuid
from types import SimpleNamespace
from typing import cast

from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SetChatMenuButton
from aiogram.types import MenuButtonWebApp
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.dispatcher import create_dispatcher, setup_menu_button
from app.bot.routers.photos import on_photo_message
from app.bot.routers.stop import on_generation_stopped
from app.config import Settings
from app.services.generation import ActiveGeneration, GenerationRegistry


def test_dispatcher_exposes_generation_objects_in_workflow_data() -> None:
    """generation_service/generation_registry попадают в dp.workflow_data (DI по имени)."""
    settings = Settings()
    session_factory = async_sessionmaker()
    generation_service = SimpleNamespace(name="generation_service")
    generation_registry = SimpleNamespace(name="generation_registry")

    dp = create_dispatcher(
        settings,
        session_factory,
        generation_service=cast("object", generation_service),
        generation_registry=cast("object", generation_registry),
    )

    assert dp["settings"] is settings
    assert dp["generation_service"] is generation_service
    assert dp["generation_registry"] is generation_registry


def _register_active(
    registry: GenerationRegistry, tg_chat_id: int, draft_id: int, cancel: asyncio.Event
) -> asyncio.Task[None]:
    """Зарегистрировать реальную ActiveGeneration с фиктивной задачей."""

    async def _never() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_never())
    registry.register(
        ActiveGeneration(
            task=task,
            cancellation=cancel,
            draft_id=draft_id,
            tg_chat_id=tg_chat_id,
            chat_id=uuid.uuid4(),
        )
    )
    return task


async def test_stop_handler_cancels_registered_generation() -> None:
    """stopped_message_generation выставляет cancellation активной генерации."""
    registry = GenerationRegistry()
    cancel = asyncio.Event()
    task = _register_active(registry, tg_chat_id=1, draft_id=2, cancel=cancel)

    event = SimpleNamespace(chat=SimpleNamespace(id=1), draft_id=2)
    await on_generation_stopped(event, registry)

    assert cancel.is_set()
    task.cancel()


async def test_stop_handler_unknown_generation_is_noop() -> None:
    """Stop по неизвестному (chat_id, draft_id) не падает; stop() возвращает False."""
    registry = GenerationRegistry()
    event = SimpleNamespace(chat=SimpleNamespace(id=10), draft_id=20)

    await on_generation_stopped(event, registry)
    assert await registry.stop(10, 20) is False


class _FakePhotoBot:
    """get_file/download_file для photos handler (без Telegram API)."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def get_file(self, file_id: str) -> SimpleNamespace:
        return SimpleNamespace(file_path="photos/file_1.jpg", file_size=len(self._data))

    async def download_file(self, file_path: str) -> io.BytesIO:
        return io.BytesIO(self._data)


class _FakePhotoMessage:
    """Минимальный Message для on_photo_message: photo/caption/chat/answer."""

    def __init__(self, *, photo: list[SimpleNamespace], caption: str | None) -> None:
        self.photo = photo
        self.caption = caption
        self.chat = SimpleNamespace(id=123)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        self.answers.append(text)


class _FakeGenerationService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


async def test_photo_handler_passes_telegram_file_id_and_metadata() -> None:
    """A18: image part несёт telegram_file_id + metadata_json (unique_id/размеры/вес)."""
    data = b"\xff\xd8\xff\xe0" + b"0" * 100
    photo = SimpleNamespace(
        file_id="AgACAgIAAxkBAAIB",
        file_unique_id="AQAD-QatrkG4AA",
        width=800,
        height=600,
        file_size=len(data),
    )
    message = _FakePhotoMessage(photo=[photo], caption="что на фото?")
    generation = _FakeGenerationService()

    await on_photo_message(
        message,
        user=SimpleNamespace(id=uuid.uuid4()),
        permissions=SimpleNamespace(),
        bot=_FakePhotoBot(data),
        generation_service=generation,
        settings=Settings(),  # photo_max_bytes default 15 MB — фото проходит
    )

    assert message.answers == []  # без ошибок пользователю
    assert len(generation.calls) == 1
    parts = generation.calls[0]["current_parts"]
    image = parts[0]
    assert image["type"] == "image"
    assert image["telegram_file_id"] == "AgACAgIAAxkBAAIB"
    assert image["mime_type"] == "image/jpeg"
    assert base64.b64decode(image["data_base64"]) == data
    assert image["metadata_json"] == {
        "file_unique_id": "AQAD-QatrkG4AA",
        "width": 800,
        "height": 600,
        "file_size": len(data),
    }
    assert parts[1] == {"type": "text", "text": "что на фото?"}


class _FakeMenuBot:
    """set_chat_menu_button с опциональным сбоем."""

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._failures = failures or []

    async def set_chat_menu_button(self, **kwargs: object) -> bool:
        self.calls.append(("set_chat_menu_button", kwargs))
        if self._failures:
            raise self._failures.pop(0)
        return True


async def test_setup_menu_button_sets_global_webapp_button() -> None:
    """A02: глобальная menu button (БЕЗ chat_id) → Mini App настроек."""
    bot = _FakeMenuBot()
    settings = Settings(app_base_url="https://app.example.com")

    await setup_menu_button(bot, settings)

    assert len(bot.calls) == 1
    _, kwargs = bot.calls[0]
    assert "chat_id" not in kwargs  # глобально, для всех чатов
    button = kwargs["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "⚙️ Настройки"
    assert button.web_app.url == "https://app.example.com"


async def test_setup_menu_button_swallows_telegram_error() -> None:
    """A02: ошибка setChatMenuButton логируется и не прерывает startup."""
    error = TelegramAPIError(method=SetChatMenuButton(), message="Forbidden")
    bot = _FakeMenuBot(failures=[error])

    await setup_menu_button(bot, Settings())  # не поднимает
    assert len(bot.calls) == 1
