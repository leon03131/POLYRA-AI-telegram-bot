"""Unit-тесты compaction и автоназваний (app.context.compactor / titles).

БД нет: ContextCompactor зависит от Protocol'ов SummaryStore/MessageStore,
здесь — in-memory фейки. llm_stream — скриптованный async-генератор событий.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.context.compactor import (
    ContextCompactor,
    SummaryState,
    _normalize_summary,
    extract_json_object,
)
from app.context.titles import TitleGenerator, sanitize_title
from app.llm.base import LLMRequest
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta

# --- Фейки -----------------------------------------------------------------------


def _msg(role: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        parts=[SimpleNamespace(type="text", text=text)],
    )


class FakeSummaryStore:
    def __init__(self, state: SummaryState | None = None) -> None:
        self.state = state
        self.saved: list[dict[str, Any]] = []

    async def load(self, chat_id: uuid.UUID) -> SummaryState | None:
        return self.state

    async def save(
        self,
        chat_id: uuid.UUID,
        *,
        summary: dict[str, Any],
        covered_until_message_id: uuid.UUID,
        covered_messages_count: int,
    ) -> None:
        self.saved.append(
            {
                "chat_id": chat_id,
                "summary": summary,
                "covered_until_message_id": covered_until_message_id,
                "covered_messages_count": covered_messages_count,
            }
        )
        self.state = SummaryState(
            summary=summary, covered_until_message_id=covered_until_message_id
        )


class FakeMessageStore:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self._messages = messages

    async def list_all(self, chat_id: uuid.UUID) -> list[SimpleNamespace]:
        return list(self._messages)


def _stream_fn(
    responses: list[list[LLMEvent]],
) -> tuple[Callable[[LLMRequest], AsyncIterator[LLMEvent]], list[LLMRequest]]:
    """llm_stream-фабрика: i-й вызов получает i-й сценарий (последний повторяется)."""
    calls: list[LLMRequest] = []

    def stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)
        events = responses[min(len(calls), len(responses)) - 1]

        async def gen() -> AsyncIterator[LLMEvent]:
            for event in events:
                yield event

        return gen()

    return stream, calls


def _compactor(
    *,
    stream: Callable[[LLMRequest], AsyncIterator[LLMEvent]],
    summaries: FakeSummaryStore,
    messages: FakeMessageStore,
    keep_recent: int = 2,
    min_segment: int = 3,
) -> ContextCompactor:
    return ContextCompactor(
        llm_stream=stream,
        summary_model="gemini-3.5-flash-lite",
        summary_thinking="medium",
        keep_recent=keep_recent,
        min_segment=min_segment,
        summary_store=summaries,
        message_store=messages,
    )


_VALID_JSON = (
    '{"conversation_summary": "обсуждали котов", "important_facts": ["есть кот"],'
    ' "decisions": [], "open_threads": ["имя кота"], "user_preferences": [],'
    ' "entities": ["кот"]}'
)

# --- extract_json_object ----------------------------------------------------------


def test_extract_json_object_bare_and_fenced() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('текст вокруг {"a": 1} хвост') == {"a": 1}


def test_extract_json_object_rejects_garbage_and_non_objects() -> None:
    assert extract_json_object("") is None
    assert extract_json_object("совсем не json") is None
    assert extract_json_object('["a", "b"]') is None  # не объект
    assert extract_json_object('{"a": ') is None


# --- ContextCompactor.maybe_compact ------------------------------------------------


async def test_segment_below_min_segment_no_llm_call() -> None:
    messages = [_msg("user", f"m{i}") for i in range(4)]
    summaries = FakeSummaryStore()
    stream, calls = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    # end = 4-2 = 2 → segment = 2 сообщения < min_segment=3.
    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert calls == []
    assert summaries.saved == []


async def test_successful_compaction_upserts_summary() -> None:
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, calls = _stream_fn([[ReasoningDelta("думаю"), TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))
    chat_id = uuid.uuid4()

    assert await compactor.maybe_compact(chat_id) is True

    assert len(calls) == 1
    request = calls[0]
    assert request.model == "gemini-3.5-flash-lite"
    assert request.thinking == "medium"
    assert request.max_output_tokens == 2048

    assert len(summaries.saved) == 1
    saved = summaries.saved[0]
    assert saved["chat_id"] == chat_id
    # end = 8-2 = 6 → покрыто 6 сообщений, covered_until = messages[5].
    assert saved["covered_until_message_id"] == messages[5].id
    assert saved["covered_messages_count"] == 6
    assert saved["summary"]["conversation_summary"] == "обсуждали котов"
    assert saved["summary"]["important_facts"] == ["есть кот"]
    assert saved["summary"]["open_threads"] == ["имя кота"]


async def test_invalid_json_then_repair_succeeds() -> None:
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, calls = _stream_fn(
        [
            [TextDelta("вообще не json"), Done("stop")],
            [TextDelta(f"```json\n{_VALID_JSON}\n```"), Done("stop")],
        ]
    )
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    assert await compactor.maybe_compact(uuid.uuid4()) is True
    assert len(calls) == 2  # ровно один repair
    repair_prompt = calls[1].messages[0]["parts"][0]["text"]
    assert "валидный JSON" in repair_prompt
    assert len(summaries.saved) == 1


async def test_invalid_json_twice_gives_up() -> None:
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, calls = _stream_fn(
        [
            [TextDelta("не json"), Done("stop")],
            [TextDelta("всё ещё не json"), Done("stop")],
        ]
    )
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert len(calls) == 2  # без бесконечного цикла
    assert summaries.saved == []


async def test_covered_until_moves_forward() -> None:
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore(
        SummaryState(
            summary={"conversation_summary": "старая сводка"},
            covered_until_message_id=messages[2].id,
        )
    )
    stream, calls = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(
        stream=stream,
        summaries=summaries,
        messages=FakeMessageStore(messages),
        min_segment=2,
    )

    assert await compactor.maybe_compact(uuid.uuid4()) is True
    # segment = messages[3:6] (после покрытого messages[2], кроме recent=2).
    assert summaries.saved[0]["covered_until_message_id"] == messages[5].id
    assert summaries.saved[0]["covered_messages_count"] == 6
    prompt = calls[0].messages[0]["parts"][0]["text"]
    assert "старая сводка" in prompt  # предыдущая сводка передана в prompt


async def test_no_new_segment_when_already_covered() -> None:
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore(
        SummaryState(summary={"conversation_summary": "x"}, covered_until_message_id=messages[5].id)
    )
    stream, calls = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(
        stream=stream,
        summaries=summaries,
        messages=FakeMessageStore(messages),
        min_segment=1,
    )

    # end = 6, start = 6 → сегмента нет.
    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert calls == []


# --- A17: строгая summary -------------------------------------------------------


def test_normalize_summary_strict_validation() -> None:
    # {} / пустой текст / неправильные типы / отсутствие ключей → None.
    assert _normalize_summary({}) is None
    assert _normalize_summary({"conversation_summary": ""}) is None
    assert _normalize_summary({"conversation_summary": "   "}) is None
    wrong_types = {
        "conversation_summary": 123,
        "important_facts": [],
        "decisions": [],
        "open_threads": [],
        "user_preferences": [],
        "entities": [],
    }
    assert _normalize_summary(wrong_types) is None
    missing_lists = {"conversation_summary": "нормальный текст"}
    assert _normalize_summary(missing_lists) is None  # нет list-ключей
    not_a_list = {**missing_lists, "important_facts": "не список"}
    assert _normalize_summary(not_a_list) is None
    ok = _normalize_summary(
        {
            "conversation_summary": "  пересказ  ",
            "important_facts": ["факт", 42],
            "decisions": [],
            "open_threads": [],
            "user_preferences": [],
            "entities": [],
        }
    )
    assert ok is not None
    assert ok["conversation_summary"] == "пересказ"
    assert ok["important_facts"] == ["факт", "42"]


async def test_empty_json_object_rejected_boundary_not_moved() -> None:
    # "{}" парсится как dict, но невалиден как сводка → неудача, boundary стоит.
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, calls = _stream_fn([[TextDelta("{}"), Done("stop")]])  # repair → тоже {}
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert len(calls) == 2  # ровно один repair
    assert summaries.saved == []
    assert summaries.state is None  # covered_until не продвинут


async def test_wrong_typed_summary_rejected_boundary_not_moved() -> None:
    wrong = (
        '{"conversation_summary": 123, "important_facts": [], "decisions": [],'
        ' "open_threads": [], "user_preferences": [], "entities": []}'
    )
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, _ = _stream_fn([[TextDelta(wrong), Done("stop")]])
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert summaries.saved == []


async def test_missing_keys_summary_rejected_boundary_not_moved() -> None:
    incomplete = '{"conversation_summary": "текст без обязательных списков"}'
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, _ = _stream_fn([[TextDelta(incomplete), Done("stop")]])
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert summaries.saved == []


async def test_invalid_json_then_valid_repair_still_saved() -> None:
    # repair с ВАЛИДНОЙ сводкой — успех (строгость не мешает штатному repair).
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    stream, _ = _stream_fn(
        [
            [TextDelta("{}"), Done("stop")],
            [TextDelta(_VALID_JSON), Done("stop")],
        ]
    )
    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))

    assert await compactor.maybe_compact(uuid.uuid4()) is True
    assert len(summaries.saved) == 1


# --- A17: сериализация per chat_id и monotonic boundary -------------------------


async def test_concurrent_compaction_same_chat_serialized() -> None:
    messages = [_msg("user", f"m{i}") for i in range(8)]
    summaries = FakeSummaryStore()
    gate = asyncio.Event()
    calls: list[LLMRequest] = []

    def stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        calls.append(request)

        async def gen() -> AsyncIterator[LLMEvent]:
            if len(calls) == 1:
                await gate.wait()  # первая compaction висит внутри LLM-вызова
            yield TextDelta(_VALID_JSON)
            yield Done("stop")

        return gen()

    compactor = _compactor(stream=stream, summaries=summaries, messages=FakeMessageStore(messages))
    chat_id = uuid.uuid4()

    first = asyncio.create_task(compactor.maybe_compact(chat_id))
    await asyncio.sleep(0)  # первая дошла до LLM и висит на gate
    assert len(calls) == 1
    second = asyncio.create_task(compactor.maybe_compact(chat_id))
    await asyncio.sleep(0.05)  # у второй было время отработать
    assert len(calls) == 1  # вторая ждёт per-chat lock — LLM не вызван
    gate.set()
    assert await first is True
    # вторая стартовала ПОСЛЕ сохранения первой: сегмент уже покрыт → no-op.
    assert await second is False
    assert len(calls) == 1  # двух параллельных LLM-вызовов не было
    assert len(summaries.saved) == 1


class _ScriptedSummaryStore(FakeSummaryStore):
    """load() возвращает заскриптованные состояния по очереди, затем последнее."""

    def __init__(self, loads: list[SummaryState | None]) -> None:
        super().__init__(loads[0] if loads else None)
        self._loads = list(loads)

    async def load(self, chat_id: uuid.UUID) -> SummaryState | None:
        if self._loads:
            state = self._loads.pop(0)
            self.state = state
            return state
        return self.state


async def test_boundary_never_moves_backwards() -> None:
    # Snapshot устарел: пока LLM думала, boundary продвинул другой writer → skip.
    messages = [_msg("user", f"m{i}") for i in range(8)]
    stale = SummaryState(
        summary={"conversation_summary": "старая"}, covered_until_message_id=messages[2].id
    )
    advanced = SummaryState(
        summary={"conversation_summary": "новая"}, covered_until_message_id=messages[7].id
    )
    summaries = _ScriptedSummaryStore([stale, advanced])
    stream, _ = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(
        stream=stream,
        summaries=summaries,
        messages=FakeMessageStore(messages),
        min_segment=2,
    )

    # Сегмент из stale: messages[3:6] → новый boundary m5 ПОЗЖЕ m2, но fresh
    # состояние уже на m7 → сохранение откатило бы boundary → запрещено.
    assert await compactor.maybe_compact(uuid.uuid4()) is False
    assert summaries.saved == []
    assert summaries.state is not None
    assert summaries.state.covered_until_message_id == messages[7].id  # не откатился


async def test_unknown_existing_boundary_allows_safe_recompute() -> None:
    # Текущий covered_until не найден в истории (её чистили) → новый валиден.
    messages = [_msg("user", f"m{i}") for i in range(8)]
    stale = SummaryState(
        summary={"conversation_summary": "старая"}, covered_until_message_id=messages[2].id
    )
    orphaned = SummaryState(
        summary={"conversation_summary": "чужая"}, covered_until_message_id=uuid.uuid4()
    )
    summaries = _ScriptedSummaryStore([stale, orphaned])
    stream, _ = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(
        stream=stream,
        summaries=summaries,
        messages=FakeMessageStore(messages),
        min_segment=2,
    )

    assert await compactor.maybe_compact(uuid.uuid4()) is True
    assert summaries.saved[0]["covered_until_message_id"] == messages[5].id


# --- A17: порционный compaction (ограничение prompt) ----------------------------


async def test_long_segment_portioned_prefix_no_data_loss() -> None:
    """A17: длинный сегмент compaction идёт порциями с НАЧАЛА; boundary — только
    на реально включённый префикс; середина НЕ вырезается (потерь нет)."""
    # 38 сообщений сегмента по ~1010 символов → диалог ~38k > лимита 12k.
    messages = [_msg("user", f"{i:03}-" + "x" * 1000) for i in range(40)]
    summaries = FakeSummaryStore()
    stream, calls = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    compactor = _compactor(
        stream=stream,
        summaries=summaries,
        messages=FakeMessageStore(messages),
        keep_recent=2,
        min_segment=3,
    )

    assert await compactor.maybe_compact(uuid.uuid4()) is True
    prompt = calls[0].messages[0]["parts"][0]["text"]
    # включён только префикс, умещающийся в бюджет (~12 сообщений по ~1010)
    assert "000-" in prompt
    assert "пропущена" not in prompt  # маркера элизии больше нет
    covered_id = summaries.saved[0]["covered_until_message_id"]
    covered_pos = next(i for i, m in enumerate(messages) if m.id == covered_id)
    # boundary строго внутри сегмента, не на конце — остаток покроет следующий запуск
    assert covered_pos < 37
    # все включённые в prompt сообщения действительно покрыты boundary
    last_in_prompt_pos = max(
        (i for i, m in enumerate(messages) if f"{i:03}-" in prompt), default=-1
    )
    assert last_in_prompt_pos == covered_pos


# --- sanitize_title ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Deep dive into Python"', "Deep dive into Python"),  # двойные кавычки
        ("«Сказка про котиков»", "Сказка про котиков"),  # ёлочки
        ("'Разбор старого бага'", "Разбор старого бага"),  # одинарные
        ("строка один\nстрока два\nстрока три", "строка один строка два строка три"),
        ("один два три четыре пять шесть семь восемь", "один два три четыре пять шесть семь"),
        ("  много   пробелов   между   словами  ", "много пробелов между словами"),
    ],
)
def test_sanitize_title_ok(raw: str, expected: str) -> None:
    assert sanitize_title(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        '""',  # только кавычки → пусто
        "одно",
        "Слишком коротко",  # 2 слова < 3
    ],
)
def test_sanitize_title_none(raw: str) -> None:
    assert sanitize_title(raw) is None


def test_sanitize_title_truncates_to_60_chars() -> None:
    raw = "а" * 30 + " " + "б" * 30 + " " + "в" * 30  # 92 символа, 3 слова
    title = sanitize_title(raw)
    assert title is not None
    assert len(title) <= 60


# --- TitleGenerator -------------------------------------------------------------------


class _FakeChatsRepo:
    """Подмена ChatRepository в app.context.titles (monkeypatch)."""

    def __init__(self, session: Any) -> None:
        self.renamed: list[tuple[uuid.UUID, str]] = []
        self.chat: Any = SimpleNamespace(title=None)

    async def get(self, chat_id: uuid.UUID) -> Any:
        return self.chat

    async def rename(self, chat_id: uuid.UUID, title: str) -> Any:
        self.renamed.append((chat_id, title))
        self.chat.title = title
        return self.chat


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def commit(self) -> None:
        pass


def _title_generator(
    monkeypatch: pytest.MonkeyPatch,
    stream: Callable[[LLMRequest], AsyncIterator[LLMEvent]],
    repo: _FakeChatsRepo,
) -> TitleGenerator:
    monkeypatch.setattr("app.context.titles.ChatRepository", lambda session: repo)
    return TitleGenerator(
        session_factory=_FakeSession,
        llm_stream=stream,
        title_model="gemini-3.5-flash-lite",
        title_thinking="low",
    )


async def test_title_generator_sets_sanitized_title(monkeypatch: pytest.MonkeyPatch) -> None:
    stream, calls = _stream_fn([[TextDelta('{"title": "«Котики и собачки»"}'), Done("stop")]])
    repo = _FakeChatsRepo(None)
    generator = _title_generator(monkeypatch, stream, repo)
    chat_id = uuid.uuid4()

    assert (
        await generator.generate_and_set(chat_id, "привет, расскажи про котов", "конечно") is True
    )
    assert repo.renamed == [(chat_id, "Котики и собачки")]
    assert calls[0].model == "gemini-3.5-flash-lite"
    assert calls[0].thinking == "low"


async def test_title_generator_skips_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    stream, _ = _stream_fn([[TextDelta("название без json"), Done("stop")]])
    repo = _FakeChatsRepo(None)
    generator = _title_generator(monkeypatch, stream, repo)

    assert await generator.generate_and_set(uuid.uuid4(), "u", "a") is False
    assert repo.renamed == []


async def test_title_generator_does_not_overwrite_existing_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream, _ = _stream_fn([[TextDelta('{"title": "Новое имя чата"}'), Done("stop")]])
    repo = _FakeChatsRepo(None)
    repo.chat.title = "Ручное название"
    generator = _title_generator(monkeypatch, stream, repo)

    assert await generator.generate_and_set(uuid.uuid4(), "u", "a") is False
    assert repo.renamed == []
