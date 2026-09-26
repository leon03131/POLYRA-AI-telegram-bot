"""Unit-тесты хелперов commands-роутера: callback_data, URL Mini App, label кнопок."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.bot.routers import commands as commands_module
from app.bot.routers.commands import (
    _chat_button_label,
    build_admin_url,
    build_open_chat_callback,
    open_chat,
    parse_open_chat_callback,
)


def test_build_admin_url_uses_hash_route() -> None:
    """A02: /admin → hash route (frontend на HashRouter, прямой путь даёт 404)."""
    assert build_admin_url("https://app.example.com") == "https://app.example.com/#/admin"


def test_build_admin_url_strips_trailing_slash() -> None:
    assert build_admin_url("https://app.example.com/") == "https://app.example.com/#/admin"


def test_build_callback_has_prefix_and_uuid() -> None:
    chat_id = uuid.uuid4()
    data = build_open_chat_callback(chat_id)
    assert data.startswith("chat:open:")
    assert str(chat_id) in data


def test_parse_build_roundtrip() -> None:
    chat_id = uuid.uuid4()
    assert parse_open_chat_callback(build_open_chat_callback(chat_id)) == chat_id


def test_parse_invalid_uuid_returns_none() -> None:
    assert parse_open_chat_callback("chat:open:xyz") is None
    assert parse_open_chat_callback("chat:open:") is None


def test_parse_empty_or_foreign_prefix_returns_none() -> None:
    assert parse_open_chat_callback("") is None
    assert parse_open_chat_callback(f"other:open:{uuid.uuid4()}") is None


# --- round4-P1: label кнопки /chats ≤64 символов (InlineKeyboardButton.text) ---


def test_chat_button_label_short_title_unchanged() -> None:
    """round4-P1: короткий title проходит без изменений."""
    assert _chat_button_label("Мой чат", "01.02.2026") == "Мой чат · 01.02.2026"


def test_chat_button_label_truncates_256_char_title() -> None:
    """round4-P1: title 256 симв (максимум Mini App) → итог ≤64, дата сохранена."""
    label = _chat_button_label("ы" * 256, "01.02.2026")
    assert len(label) <= 64
    assert label.endswith("… · 01.02.2026")


def test_chat_button_label_empty_or_none_title() -> None:
    """round4-P1: пустой/None title → заглушка «Без названия»."""
    assert _chat_button_label(None, "01.02.2026") == "Без названия · 01.02.2026"
    assert _chat_button_label("", "01.02.2026") == "Без названия · 01.02.2026"


def test_chat_button_label_unicode_emoji() -> None:
    """round4-P1: лимит Bot API — по символам; эмодзи-титул тоже режется корректно."""
    label = _chat_button_label("😀🎉🚀" * 30, "01.02.2026")
    assert len(label) <= 64
    assert label.endswith("… · 01.02.2026")


def test_chat_button_label_marker_counts_toward_limit() -> None:
    """round4-P1: маркер «✅ » текущего чата учитывается в общем лимите 64."""
    label = _chat_button_label("т" * 100, "01.02.2026", marker="✅ ")
    assert len(label) <= 64
    assert label.startswith("✅ ")
    assert label.endswith("… · 01.02.2026")


# --- round4-P2: подтверждение open_chat без parse_mode (default бота — HTML) ---


class _FakeChatMessage:
    """Message в ветке open_chat: пишет send (text, kwargs)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        self.sent.append((text, kwargs))


class _FakeCallback:
    """CallbackQuery для open_chat: data + message + answers."""

    def __init__(self, data: str, message: _FakeChatMessage) -> None:
        self.data = data
        self.message = message
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        self.answers.append(text)


class _FakeSession:
    """Async-сессия-заглушка (commit — no-op)."""

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        return None


async def test_open_chat_confirmation_sent_with_parse_mode_none(monkeypatch) -> None:
    """round4-P2: «Открыт чат: …» шлётся с parse_mode=None, title не экранируется.

    У бота default parse_mode=HTML (dispatcher), а chat.title из Mini App
    может содержать '<' — без явного parse_mode=None это TelegramBadRequest.
    """
    chat_id = uuid.uuid4()
    user_id = uuid.uuid4()
    title = "Чат <про> углы"

    class _FakeRepo:
        def __init__(self, session: object) -> None:
            pass

        async def get(self, chat_id_arg: object) -> object:
            assert chat_id_arg == chat_id
            return SimpleNamespace(id=chat_id, owner_user_id=user_id, title=title)

    class _FakeChatService:
        def __init__(self, session: object) -> None:
            pass

        async def set_current_chat(self, user_id_arg: object, chat_id_arg: object) -> None:
            return None

    # isinstance(callback.message, Message) в open_chat смотрит глобал модуля.
    monkeypatch.setattr(commands_module, "Message", _FakeChatMessage)
    monkeypatch.setattr(commands_module, "ChatRepository", _FakeRepo)
    monkeypatch.setattr(commands_module, "ChatService", _FakeChatService)

    message = _FakeChatMessage()
    callback = _FakeCallback(build_open_chat_callback(chat_id), message)
    user = SimpleNamespace(id=user_id)

    await open_chat(callback, user, lambda: _FakeSession())

    assert callback.answers == ["Чат выбран"]
    assert len(message.sent) == 1
    text, kwargs = message.sent[0]
    assert kwargs.get("parse_mode") is None  # round4-P2: без HTML-парсинга
    assert text == f"Открыт чат: {title}"
