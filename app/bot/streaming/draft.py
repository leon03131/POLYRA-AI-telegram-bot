"""Стриминг ответа модели в Telegram через message drafts (Bot API 10.x).

Fallback-цепочка на каждый flush:
    Tier 1: send_rich_message_draft (InputRichMessage(markdown=...)).
    Tier 2: send_message_draft (plain text, пустой text = плейсхолдер «Thinking…»).
    Tier 3: send_message + edit_message_text (throttled edit).

Драфты эфемерны (~30 с превью), поэтому финальный текст обязательно
отправляется отдельным сообщением через :meth:`DraftStreamer.finalize`.
Downgrade tier1→tier2→tier3 односторонний (на время жизни генерации);
сетевые/прочие ошибки не меняют tier и не роняют flush — следующий вызов
ретраит тот же tier.

Rate limit драфтов не документирован → клиентский throttle
(``throttle_interval``, по умолчанию 1.0 с) + терпимость к ошибкам.
"""

import logging
import time
import uuid
from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InputRichMessage, Message

logger = logging.getLogger(__name__)

# Лимиты Bot API: rich message — 32768 символов; draft text / message text — 4096.
# В драфтах и edit оставляем запас под префикс «…\n» (tail-режим для длинного текста).
RICH_LIMIT = 32768
MESSAGE_LIMIT = 4096
RICH_DRAFT_TAIL = 32700
DRAFT_TAIL = 4000
MESSAGE_TAIL = 4000

TAIL_PREFIX = "…\n"


def new_draft_id() -> int:
    """Случайный положительный int64 != 0 (uuid4-based).

    Bot API требует draft_id != 0; тот же draft_id = анимированное обновление
    драфта, поэтому id должен быть уникален на активную генерацию.
    """
    return uuid.uuid4().int >> 65 or 1


def sanitize_partial_markdown(text: str) -> str:
    """Закрыть незакрытые markdown-конструкции частичного снапшота.

    Эвристика (не полноценный парсер):
    - нечётное число fenced-блоков (строки, начинающиеся с ```) →
      в конец дописывается перевод строки и закрывающий ``` fence;
    - нечётное число обратных кавычек в последней строке (без учёта fence-маркеров
      ```) → дописывается одна ` (грубая эвристика для незакрытого inline-code
      на конце снапшота).
    Цель — только избежать BadRequest на partial-снапшотах; валидность
    остальной разметки не гарантируется.
    """
    if sum(1 for line in text.splitlines() if line.lstrip().startswith("```")) % 2 == 1:
        text = f"{text}\n```"
    last_line = text.rsplit("\n", 1)[-1]
    if last_line.replace("```", "").count("`") % 2 == 1:
        text += "`"
    return text


def _tail(text: str, limit: int) -> str:
    """Последние ``limit`` символов текста с префиксом «…\\n», если обрезано."""
    if len(text) <= limit:
        return text
    return TAIL_PREFIX + text[-limit:]


def _split_text(text: str, limit: int) -> list[str]:
    """Разбить текст на части <= limit, предпочитая границу по последнему \\n."""
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:  # перевода строки нет — режем по лимиту
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts


class DraftStreamer:
    """Стримит накопленный текст в Telegram-драфт с throttle и fallback-цепочкой."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        draft_id: int,
        *,
        throttle_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._draft_id = draft_id
        self._throttle_interval = throttle_interval
        self._clock = clock
        self._text = ""
        self._tier = 1
        self._last_flush: float | None = None
        self._fallback_message_id: int | None = None

    async def append(self, delta: str) -> None:
        """Накопить ``delta``; авто-flush, если прошло >= throttle_interval."""
        self._text += delta
        if self._last_flush is not None and self._due():
            await self.flush()

    async def flush(self, *, force: bool = False) -> None:
        """Отправить текущий снапшот текущим tier'ом. Ошибки не поднимаются."""
        if not force and not self._due():
            return
        if self._tier == 1:
            await self._flush_rich_draft()
        elif self._tier == 2:
            await self._flush_plain_draft()
        else:
            await self._flush_message_edit()

    async def finalize(self) -> Message | None:
        """Отправить финальный текст персистентным сообщением. None, если пусто/ошибка.

        Tier 3 — особый случай: сообщение уже существует (send+edit цикл).
        Финализируем его EDIT'ом, а не новым сообщением (иначе пользователь
        увидел бы ответ дважды).
        """
        text = self._text
        if not text:
            return None
        if self._tier == 3 and self._fallback_message_id is not None:
            return await self._finalize_existing_message(text)
        try:
            return await self._bot.send_rich_message(
                self._chat_id,
                rich_message=InputRichMessage(markdown=_tail(text, RICH_LIMIT)),
            )
        except TelegramAPIError as exc:
            logger.info("send_rich_message failed, fallback to send_message: %s", exc)
        try:
            first: Message | None = None
            for part in _split_text(text, MESSAGE_LIMIT):
                message = await self._bot.send_message(self._chat_id, part)
                if first is None:
                    first = message
            return first
        except TelegramAPIError:
            logger.exception("finalize failed for chat %s", self._chat_id)
            return None

    async def _finalize_existing_message(self, text: str) -> Message | None:
        """Tier 3: отредактировать существующее сообщение финальным текстом.

        Переполнение MESSAGE_LIMIT: первая часть — edit, остальное — новыми
        сообщениями. При сбое edit — fallback на отправку новых сообщений.
        """
        parts = _split_text(text, MESSAGE_LIMIT)
        try:
            edited = await self._bot.edit_message_text(
                parts[0], chat_id=self._chat_id, message_id=self._fallback_message_id
            )
            for part in parts[1:]:
                await self._bot.send_message(self._chat_id, part)
            return edited if isinstance(edited, Message) else None
        except TelegramAPIError as exc:
            logger.info("tier-3 edit finalize failed, sending fresh message: %s", exc)
            first: Message | None = None
            try:
                for part in parts:
                    message = await self._bot.send_message(self._chat_id, part)
                    if first is None:
                        first = message
            except TelegramAPIError:
                logger.exception("finalize failed for chat %s", self._chat_id)
            return first

    async def fail(self, user_message: str) -> None:
        """Сообщить пользователю об ошибке; ошибки отправки гасятся."""
        try:
            await self._bot.send_message(self._chat_id, user_message)
        except TelegramAPIError:
            logger.exception("fail() could not notify chat %s", self._chat_id)

    def _due(self) -> bool:
        """True, если с последнего успешного flush прошло >= throttle_interval."""
        return (
            self._last_flush is None or self._clock() - self._last_flush >= self._throttle_interval
        )

    def _mark_flushed(self) -> None:
        self._last_flush = self._clock()

    async def _flush_rich_draft(self) -> None:
        markdown = sanitize_partial_markdown(_tail(self._text, RICH_DRAFT_TAIL))
        try:
            await self._bot.send_rich_message_draft(
                self._chat_id,
                self._draft_id,
                rich_message=InputRichMessage(markdown=markdown),
                can_stop=True,
                keep_on_stop=True,
            )
            self._mark_flushed()
        except TelegramBadRequest as exc:
            logger.info("rich draft rejected (%s), downgrade to message draft", exc)
            self._tier = 2
            await self._flush_plain_draft()
        except TelegramAPIError as exc:
            logger.warning("rich draft flush failed, will retry: %s", exc)

    async def _flush_plain_draft(self) -> None:
        # Пустой text допустим (Bot API 10.0+): клиент покажет «Thinking…».
        text = _tail(self._text, DRAFT_TAIL)
        try:
            await self._bot.send_message_draft(
                self._chat_id,
                self._draft_id,
                text=text,
                can_stop=True,
                keep_on_stop=True,
            )
            self._mark_flushed()
        except TelegramAPIError as exc:
            logger.info("message draft failed (%s), downgrade to throttled edit", exc)
            self._tier = 3
            await self._flush_message_edit()

    async def _flush_message_edit(self) -> None:
        text = _tail(self._text, MESSAGE_TAIL)
        if not text:
            return
        try:
            if self._fallback_message_id is None:
                message = await self._bot.send_message(self._chat_id, text)
                self._fallback_message_id = message.message_id
            else:
                await self._bot.edit_message_text(
                    text, chat_id=self._chat_id, message_id=self._fallback_message_id
                )
            self._mark_flushed()
        except TelegramAPIError as exc:
            logger.warning("tier-3 flush failed, will retry: %s", exc)
