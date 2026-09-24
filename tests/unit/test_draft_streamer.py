"""Unit-тесты DraftStreamer (app.bot.streaming.draft)."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.methods import SendMessageDraft, SendRichMessageDraft
from aiogram.types import InputRichMessage

from app.bot.streaming import DraftStreamer, new_draft_id
from app.bot.streaming.draft import sanitize_partial_markdown

CHAT_ID = 42


def _bad_request(message: str = "Bad Request") -> TelegramBadRequest:
    method = SendRichMessageDraft(
        chat_id=CHAT_ID, draft_id=1, rich_message=InputRichMessage(markdown="x")
    )
    return TelegramBadRequest(method=method, message=message)


def _api_error(message: str = "Gateway Timeout") -> TelegramAPIError:
    method = SendMessageDraft(chat_id=CHAT_ID, draft_id=1, text="x")
    return TelegramAPIError(method=method, message=message)


class FakeBot:
    """Записывает вызовы Bot-методов; умеет падать заданными исключениями."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.failures: dict[str, list[Exception]] = {}
        self._next_message_id = 1

    def fail(self, method: str, exc: Exception, *, times: int = 1) -> None:
        self.failures.setdefault(method, []).extend([exc] * times)

    async def _call(self, method: str, **kwargs):
        self.calls.append((method, kwargs))
        queue = self.failures.get(method, [])
        if queue:
            raise queue.pop(0)
        if method in {"send_message", "send_rich_message"}:
            return SimpleNamespace(message_id=self._next_message_id)
        return True

    async def send_rich_message_draft(self, chat_id, draft_id, **kwargs):
        return await self._call(
            "send_rich_message_draft", chat_id=chat_id, draft_id=draft_id, **kwargs
        )

    async def send_message_draft(self, chat_id, draft_id, **kwargs):
        return await self._call("send_message_draft", chat_id=chat_id, draft_id=draft_id, **kwargs)

    async def send_rich_message(self, chat_id, **kwargs):
        return await self._call("send_rich_message", chat_id=chat_id, **kwargs)

    async def send_message(self, chat_id, text, **kwargs):
        return await self._call("send_message", chat_id=chat_id, text=text, **kwargs)

    async def edit_message_text(self, text, **kwargs):
        return await self._call("edit_message_text", text=text, **kwargs)

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


def make_clock() -> tuple[list[float], Callable[[], float]]:
    t = [100.0]
    return t, lambda: t[0]


def make_streamer(bot: FakeBot, clock: Callable[[], float]) -> DraftStreamer:
    return DraftStreamer(bot, CHAT_ID, draft_id=7, throttle_interval=1.0, clock=clock)


async def test_append_sends_nothing_before_interval() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    streamer = make_streamer(bot, clock)
    await streamer.append("hello")
    assert bot.calls == []


async def test_force_flush_sends_rich_draft() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    streamer = make_streamer(bot, clock)
    await streamer.append("```python\ncode")
    await streamer.flush(force=True)
    assert bot.methods() == ["send_rich_message_draft"]
    _, kwargs = bot.calls[0]
    assert kwargs["chat_id"] == CHAT_ID
    assert kwargs["draft_id"] == 7
    assert kwargs["can_stop"] is True
    assert kwargs["keep_on_stop"] is True
    rich_message = kwargs["rich_message"]
    assert isinstance(rich_message, InputRichMessage)
    assert rich_message.markdown == "```python\ncode\n```"  # fence закрыт санитайзером


async def test_throttling_two_appends_one_flush() -> None:
    bot = FakeBot()
    t, clock = make_clock()
    streamer = make_streamer(bot, clock)
    await streamer.append("a")
    await streamer.flush(force=True)  # первый (стартовый) flush
    assert len(bot.calls) == 1
    await streamer.append("b")
    await streamer.append("c")  # интервал не прошёл → авто-flush не сработает
    assert len(bot.calls) == 1
    t[0] += 2.0
    await streamer.append("d")  # теперь авто-flush
    assert len(bot.calls) == 2


async def test_bad_request_downgrades_to_plain_draft_same_flush() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    bot.fail("send_rich_message_draft", _bad_request())
    streamer = make_streamer(bot, clock)
    await streamer.append("hello")
    await streamer.flush(force=True)
    assert bot.methods() == ["send_rich_message_draft", "send_message_draft"]
    _, kwargs = bot.calls[1]
    assert kwargs["chat_id"] == CHAT_ID
    assert kwargs["draft_id"] == 7
    assert kwargs["text"] == "hello"
    assert kwargs["can_stop"] is True
    assert kwargs["keep_on_stop"] is True


async def test_tier1_and_tier2_fail_fall_back_to_message_edit() -> None:
    bot = FakeBot()
    t, clock = make_clock()
    bot.fail("send_rich_message_draft", _bad_request())
    bot.fail("send_message_draft", _bad_request("no drafts"), times=10**6)
    streamer = make_streamer(bot, clock)
    await streamer.append("hello")
    await streamer.flush(force=True)
    assert bot.methods() == ["send_rich_message_draft", "send_message_draft", "send_message"]
    t[0] += 2.0
    await streamer.append(" world")
    await streamer.flush()
    assert bot.methods()[-1] == "edit_message_text"
    _, kwargs = bot.calls[-1]
    assert kwargs["text"] == "hello world"
    assert kwargs["chat_id"] == CHAT_ID
    assert kwargs["message_id"] == 1


async def test_network_error_not_raised_and_retried() -> None:
    bot = FakeBot()
    t, clock = make_clock()
    bot.fail("send_rich_message_draft", _api_error())
    streamer = make_streamer(bot, clock)
    await streamer.append("hello")
    await streamer.flush(force=True)  # ошибка проглочена
    assert bot.methods() == ["send_rich_message_draft"]
    t[0] += 2.0  # время НЕ было обновлено → следующий flush ретраит tier 1
    await streamer.flush()
    assert bot.methods() == ["send_rich_message_draft", "send_rich_message_draft"]


async def test_finalize_sends_rich_message() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    streamer = make_streamer(bot, clock)
    await streamer.append("final **text**")
    result = await streamer.finalize()
    assert result is not None
    assert result.message_id == 1
    assert bot.methods() == ["send_rich_message"]
    _, kwargs = bot.calls[0]
    assert kwargs["rich_message"].markdown == "final **text**"


async def test_finalize_fallback_splits_long_text() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    bot.fail("send_rich_message", _api_error("no rich"))
    streamer = make_streamer(bot, clock)
    text = "x" * 5000
    await streamer.append(text)
    result = await streamer.finalize()
    assert result is not None
    assert result.message_id == 1  # возвращается первая часть
    assert bot.methods() == ["send_rich_message", "send_message", "send_message"]
    parts = [kwargs["text"] for _, kwargs in bot.calls[1:]]
    assert all(len(part) <= 4096 for part in parts)
    assert "".join(parts) == text


async def test_finalize_empty_text_sends_nothing() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    streamer = make_streamer(bot, clock)
    assert await streamer.finalize() is None
    assert bot.calls == []


def test_sanitize_closes_unclosed_fence() -> None:
    assert sanitize_partial_markdown("```python\ncode") == "```python\ncode\n```"


def test_sanitize_keeps_closed_fence() -> None:
    text = "```\nok\n```"
    assert sanitize_partial_markdown(text) == text


async def test_fail_swallows_bot_error() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    bot.fail("send_message", _api_error("dead"))
    streamer = make_streamer(bot, clock)
    await streamer.fail("Произошла ошибка")  # не должно поднять исключение
    assert bot.methods() == ["send_message"]


def test_new_draft_id_positive_and_unique() -> None:
    ids = {new_draft_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(isinstance(i, int) and i > 0 for i in ids)


async def test_empty_draft_sends_thinking_placeholder() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    bot.fail("send_rich_message_draft", _bad_request())
    streamer = make_streamer(bot, clock)
    await streamer.flush(force=True)
    _, kwargs = bot.calls[-1]
    assert kwargs["text"] == ""  # пустой text = плейсхолдер «Thinking…»


def test_long_text_tail_prefixed() -> None:
    text = "y" * 5000
    sanitized = sanitize_partial_markdown(text)
    assert len(sanitized) == 5000  # sanitize не режет, только закрывает разметку


async def test_flush_tail_mode_for_long_text() -> None:
    bot = FakeBot()
    _, clock = make_clock()
    streamer = make_streamer(bot, clock)
    text = "y" * 33000  # больше лимита rich draft (32768) → tail-режим
    await streamer.append(text)
    await streamer.flush(force=True)
    _, kwargs = bot.calls[0]
    markdown = kwargs["rich_message"].markdown
    assert markdown.startswith("…\n")
    assert len(markdown) == 32700 + 2  # префикс + последние 32700 символов
    assert markdown.endswith("y" * 100)


async def test_finalize_tier3_edits_existing_message_instead_of_duplicate() -> None:
    """Tier 3: финал — edit существующего сообщения, НЕ новое сообщение (нет дублей)."""
    bot = FakeBot()
    t, clock = make_clock()
    streamer = make_streamer(bot, clock)
    bot.fail("send_rich_message_draft", _bad_request())
    bot.fail("send_message_draft", _api_error())

    await streamer.append("hello")
    await streamer.flush(force=True)  # tier3: первичное send_message
    t[0] += 2.0
    await streamer.append(" world")
    await streamer.flush(force=True)  # tier3: edit
    await streamer.finalize()

    methods = bot.methods()
    assert methods.count("send_message") == 1  # только первичное сообщение tier-3
    assert "send_rich_message" not in methods
    # auto-flush при append + явный flush + финал — все через edit, без новых сообщений
    final_edit = [kw for name, kw in bot.calls if name == "edit_message_text"][-1]
    assert final_edit["text"] == "hello world"


async def test_finalize_tier3_long_text_edits_then_sends_rest() -> None:
    """Tier 3 + текст > 4096: первая часть — edit, остаток — новыми сообщениями."""
    bot = FakeBot()
    t, clock = make_clock()
    streamer = make_streamer(bot, clock)
    bot.fail("send_rich_message_draft", _bad_request())
    bot.fail("send_message_draft", _api_error())

    long_text = "x" * 5000
    streamer._text = long_text  # напрямую: нас интересует финал
    await streamer.flush(force=True)  # tier3: send tail (4000)
    await streamer.finalize()

    methods = bot.methods()
    assert methods.count("edit_message_text") == 1
    assert methods.count("send_message") == 2  # tier3 первичное + «хвост» >4096
    assert "send_rich_message" not in methods
