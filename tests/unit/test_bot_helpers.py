"""Unit-тесты хелперов callback_data кнопок выбора чата и URL Mini App."""

from __future__ import annotations

import uuid

from app.bot.routers.commands import (
    build_admin_url,
    build_open_chat_callback,
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
