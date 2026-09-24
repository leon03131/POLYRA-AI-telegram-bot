"""Unit-тесты долговременной памяти (app.memory).

БД нет: retriever тестируется через фейк RetrievalStore, extractor — через
фейк MemoryStore (Protocol из app.memory.extractor) и скриптованный llm_stream.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from app.db.models import Memory
from app.llm.base import LLMRequest
from app.llm.events import Done, LLMEvent, TextDelta
from app.memory.deduplicator import find_duplicate, jaccard_tokens
from app.memory.extractor import MemoryExtractor, parse_candidates
from app.memory.normalizer import normalize_memory_text
from app.memory.retriever import retrieve_memories

# --- Фейки -----------------------------------------------------------------------


def _mem(
    *,
    user_id: uuid.UUID | None = None,
    text: str = "факт",
    normalized_text: str | None = None,
    category: str = "general",
    importance: int = 5,
    source_chat_id: uuid.UUID | None = None,
) -> Memory:
    mem = Memory(
        user_id=user_id or uuid.uuid4(),
        text=text,
        normalized_text=(
            normalized_text if normalized_text is not None else normalize_memory_text(text)
        ),
        category=category,
        importance=importance,
        source_chat_id=source_chat_id,
    )
    mem.id = uuid.uuid4()
    return mem


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


def _failing_stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
    async def gen() -> AsyncIterator[LLMEvent]:
        raise RuntimeError("provider down")
        yield  # pragma: no cover — делает gen асинхронным генератором

    return gen()


class FakeRetrievalStore:
    """Фейк RetrievalStore для retrieve_memories."""

    def __init__(self, *, fts: list[Any] | None = None, fallback: list[Any] | None = None) -> None:
        self.fts = list(fts or [])
        self.fallback = list(fallback or [])
        self.fts_calls = 0
        self.fallback_calls = 0
        self.touched: list[uuid.UUID] = []

    async def search_fts(self, user_id: uuid.UUID, query: str, *, limit: int) -> list[Any]:
        self.fts_calls += 1
        return list(self.fts)

    async def list_for_user(self, user_id: uuid.UUID, *, limit: int) -> list[Any]:
        self.fallback_calls += 1
        return list(self.fallback)

    async def touch_used(self, memory_ids: list[uuid.UUID]) -> None:
        self.touched.extend(memory_ids)


class FakeMemoryStore:
    """Фейк MemoryStore: записывает вызовы, фильтрует по user_id (изоляция)."""

    def __init__(self, existing: list[Memory] | None = None) -> None:
        self.existing: list[Memory] = list(existing or [])
        self.list_calls: list[uuid.UUID] = []
        self.add_calls: list[dict[str, Any]] = []
        self.update_calls: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any]]] = []

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        self.list_calls.append(user_id)
        return [m for m in self.existing if m.user_id == user_id][:limit]

    async def add(
        self,
        user_id: uuid.UUID,
        *,
        text: str,
        normalized_text: str,
        category: str,
        importance: int,
        source_chat_id: uuid.UUID | None = None,
        source_message_id: uuid.UUID | None = None,
    ) -> Memory:
        memory = _mem(
            user_id=user_id,
            text=text,
            normalized_text=normalized_text,
            category=category,
            importance=importance,
            source_chat_id=source_chat_id,
        )
        self.existing.append(memory)
        self.add_calls.append(
            {
                "user_id": user_id,
                "text": text,
                "normalized_text": normalized_text,
                "category": category,
                "importance": importance,
                "source_chat_id": source_chat_id,
            }
        )
        return memory

    async def update_fields(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, **fields: Any
    ) -> Memory | None:
        self.update_calls.append((memory_id, user_id, fields))
        for memory in self.existing:
            if memory.id == memory_id and memory.user_id == user_id:
                for key, value in fields.items():
                    setattr(memory, key, value)
                return memory
        return None

    async def find_by_normalized(self, user_id: uuid.UUID, normalized_text: str) -> Memory | None:
        for memory in self.existing:
            if memory.user_id == user_id and memory.normalized_text == normalized_text:
                return memory
        return None


def _extractor(
    stream: Callable[[LLMRequest], AsyncIterator[LLMEvent]],
    store: FakeMemoryStore,
    *,
    min_chars: int = 10,
    dedup_threshold: float = 0.85,
) -> MemoryExtractor:
    return MemoryExtractor(
        llm_stream=stream,
        memory_model="gemini-3.5-flash-lite",
        memory_thinking="medium",
        min_chars=min_chars,
        dedup_threshold=dedup_threshold,
        memory_store=store,
    )


# --- normalize_memory_text ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Привет,  Мир! ", "привет мир"),  # регистр + краевая пунктуация
        ("много\n\tпробелов   между", "много пробелов между"),  # collapse whitespace
        ('(скобки), «ёлочки», "кавычки"', "скобки ёлочки кавычки"),
        ("e-mail и don't", "e-mail и don't"),  # внутренняя пунктуация сохраняется
        ("... ...", ""),  # слова из одной пунктуации исчезают
        ("", ""),
    ],
)
def test_normalize_memory_text(raw: str, expected: str) -> None:
    assert normalize_memory_text(raw) == expected


# --- jaccard_tokens / find_duplicate ------------------------------------------------


def test_jaccard_tokens() -> None:
    assert jaccard_tokens("a b c", "a b c") == 1.0
    assert jaccard_tokens("a b", "c d") == 0.0
    assert jaccard_tokens("", "a b") == 0.0
    assert jaccard_tokens("a b c d", "a b c e") == pytest.approx(3 / 5)


def test_find_duplicate_exact_normalized_match() -> None:
    existing = [_mem(text="Пользователь любит кошек")]
    duplicate = find_duplicate("пользователь любит кошек", existing, threshold=0.99)
    assert duplicate is existing[0]


def test_find_duplicate_near_dup_above_threshold() -> None:
    existing = [_mem(text="Пользователь любит кошек и больших собак")]
    # 6 общих токенов из 7 → 6/7 ≈ 0.857 >= 0.85
    duplicate = find_duplicate(
        "пользователь очень любит кошек и больших собак", existing, threshold=0.85
    )
    assert duplicate is existing[0]


def test_find_duplicate_below_threshold_returns_none() -> None:
    existing = [_mem(text="Пользователь любит кошек")]
    assert find_duplicate("работает над проектом aibot", existing, threshold=0.85) is None
    # тот же near-dup, но порог выше
    assert (
        find_duplicate("пользователь очень любит кошек и больших собак", existing, threshold=0.95)
        is None
    )


def test_find_duplicate_empty_input_returns_none() -> None:
    assert find_duplicate("", [_mem()], threshold=0.85) is None


# --- retrieve_memories (fallback-логика) --------------------------------------------


async def test_retrieve_fts_hits_skip_fallback() -> None:
    hits = [_mem(), _mem()]
    store = FakeRetrievalStore(fts=hits, fallback=[_mem()])
    result = await retrieve_memories(store, uuid.uuid4(), "кошки", limit=5)
    assert result == hits
    assert store.fts_calls == 1
    assert store.fallback_calls == 0
    assert store.touched == [m.id for m in hits]


async def test_retrieve_empty_fts_falls_back_to_top_important() -> None:
    fallback = [_mem()]
    store = FakeRetrievalStore(fts=[], fallback=fallback)
    result = await retrieve_memories(store, uuid.uuid4(), "нет совпадений", limit=5)
    assert result == fallback
    assert store.fts_calls == 1
    assert store.fallback_calls == 1
    assert store.touched == [fallback[0].id]


async def test_retrieve_blank_query_skips_fts() -> None:
    fallback = [_mem()]
    store = FakeRetrievalStore(fts=[_mem()], fallback=fallback)
    result = await retrieve_memories(store, uuid.uuid4(), "   ", limit=5)
    assert result == fallback
    assert store.fts_calls == 0


async def test_retrieve_no_memories_does_not_touch() -> None:
    store = FakeRetrievalStore()
    result = await retrieve_memories(store, uuid.uuid4(), "запрос", limit=5)
    assert result == []
    assert store.touched == []


# --- parse_candidates -----------------------------------------------------------------


def test_parse_candidates_validates_and_clamps() -> None:
    data = {
        "memories": [
            {"text": "норм", "category": "fact", "importance": 7},
            {"text": "слишком важно", "category": "fact", "importance": 99},
            {"text": "неважно", "category": "fact", "importance": 0},
            {"text": "странная категория", "category": "weird", "importance": "не число"},
            {"text": "   "},  # пустой текст → skip
            "не словарь",  # → skip
            {"no_text": True},  # → skip
        ]
    }
    candidates = parse_candidates(data)
    assert [c.importance for c in candidates] == [7, 10, 1, 5]
    assert candidates[3].category == "other"
    assert all(c.text for c in candidates)


def test_parse_candidates_rejects_garbage() -> None:
    assert parse_candidates(None) == []
    assert parse_candidates({}) == []
    assert parse_candidates({"memories": "не список"}) == []


def test_parse_candidates_caps_count() -> None:
    data = {"memories": [{"text": f"факт {i}"} for i in range(12)]}
    assert len(parse_candidates(data)) == 10


def test_parse_candidates_caps_text_length() -> None:
    # Длина кандидата ограничена 500 символами (обрезка при парсинге).
    data = {"memories": [{"text": "а" * 800, "category": "fact", "importance": 5}]}
    candidates = parse_candidates(data)
    assert len(candidates) == 1
    assert len(candidates[0].text) == 500
    assert candidates[0].text == "а" * 500


# --- MemoryExtractor -------------------------------------------------------------------

_VALID_JSON = (
    '{"memories": [{"text": "Пользователь любит кошек", "category": "preference",'
    ' "importance": 7}, {"text": "Работает над проектом aibot", "category": "project",'
    ' "importance": 8}]}'
)


async def test_extractor_adds_new_memories() -> None:
    store = FakeMemoryStore()
    stream, calls = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    extractor = _extractor(stream, store)
    user_id, chat_id = uuid.uuid4(), uuid.uuid4()

    added = await extractor.extract_and_store(
        user_id=user_id,
        chat_id=chat_id,
        user_text="обожаю кошек, у меня их три",
        assistant_text="замечательно, кошки — это хорошо",
    )

    assert added == 2
    assert len(store.add_calls) == 2
    assert all(call["user_id"] == user_id for call in store.add_calls)
    assert all(call["source_chat_id"] == chat_id for call in store.add_calls)
    first = store.add_calls[0]
    assert first["text"] == "Пользователь любит кошек"
    assert first["category"] == "preference"
    assert first["importance"] == 7
    assert first["normalized_text"] == normalize_memory_text(first["text"])
    assert store.update_calls == []
    # запрос ушёл во внутреннюю модель с лимитом 1024
    assert calls[0].model == "gemini-3.5-flash-lite"
    assert calls[0].thinking == "medium"
    assert calls[0].max_output_tokens == 1024


async def test_extractor_merges_exact_duplicate_instead_of_insert() -> None:
    user_id = uuid.uuid4()
    existing = _mem(user_id=user_id, text="Пользователь любит кошек", importance=5)
    store = FakeMemoryStore([existing])
    # тот же факт с другим регистром/пунктуацией, но выше importance
    stream, _ = _stream_fn(
        [
            [
                TextDelta(
                    '{"memories": [{"text": "пользователь любит кошек!", '
                    '"category": "preference", "importance": 9}]}'
                ),
                Done("stop"),
            ]
        ]
    )
    extractor = _extractor(stream, store)

    added = await extractor.extract_and_store(
        user_id=user_id, chat_id=uuid.uuid4(), user_text="u" * 20, assistant_text="a" * 20
    )

    assert added == 0
    assert store.add_calls == []
    assert len(store.update_calls) == 1
    memory_id, call_user_id, fields = store.update_calls[0]
    assert memory_id == existing.id
    assert call_user_id == user_id
    assert fields["importance"] == 9
    assert "text" not in fields  # новый текст не длиннее — не перетираем


async def test_extractor_merges_near_duplicate_and_updates_longer_text() -> None:
    user_id, chat_id = uuid.uuid4(), uuid.uuid4()
    existing = _mem(user_id=user_id, text="Пользователь любит кошек и больших собак", importance=5)
    store = FakeMemoryStore([existing])
    stream, _ = _stream_fn(
        [
            [
                TextDelta(
                    '{"memories": [{"text": "Пользователь очень любит кошек и больших собак", '
                    '"category": "preference", "importance": 9}]}'
                ),
                Done("stop"),
            ]
        ]
    )
    extractor = _extractor(stream, store, dedup_threshold=0.85)

    added = await extractor.extract_and_store(
        user_id=user_id, chat_id=chat_id, user_text="u" * 20, assistant_text="a" * 20
    )

    assert added == 0
    assert store.add_calls == []
    _, call_user_id, fields = store.update_calls[0]
    assert call_user_id == user_id
    assert fields["importance"] == 9
    assert fields["text"] == "Пользователь очень любит кошек и больших собак"
    assert fields["normalized_text"] == "пользователь очень любит кошек и больших собак"
    assert fields["source_chat_id"] == chat_id


async def test_extractor_dedups_within_single_batch() -> None:
    store = FakeMemoryStore()
    stream, _ = _stream_fn(
        [
            [
                TextDelta(
                    '{"memories": [{"text": "Любит кошек", "importance": 5}, '
                    '{"text": "любит кошек!", "importance": 7}]}'
                ),
                Done("stop"),
            ]
        ]
    )
    extractor = _extractor(stream, store)

    added = await extractor.extract_and_store(
        user_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        user_text="u" * 20,
        assistant_text="a" * 20,
    )

    assert added == 1  # второй кандидат — дубликат только что добавленного
    assert len(store.add_calls) == 1
    assert len(store.update_calls) == 1
    assert store.update_calls[0][2]["importance"] == 7


async def test_extractor_empty_memories_returns_zero_without_store() -> None:
    store = FakeMemoryStore()
    stream, _ = _stream_fn([[TextDelta('{"memories": []}'), Done("stop")]])
    extractor = _extractor(stream, store)

    added = await extractor.extract_and_store(
        user_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        user_text="u" * 20,
        assistant_text="a" * 20,
    )

    assert added == 0
    assert store.list_calls == []  # до хранилища не дошли
    assert store.add_calls == []


async def test_extractor_invalid_json_returns_zero_without_exception() -> None:
    store = FakeMemoryStore()
    stream, _ = _stream_fn([[TextDelta("вообще не json"), Done("stop")]])
    extractor = _extractor(stream, store)

    added = await extractor.extract_and_store(
        user_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        user_text="u" * 20,
        assistant_text="a" * 20,
    )

    assert added == 0
    assert store.add_calls == []


async def test_extractor_llm_failure_returns_zero() -> None:
    store = FakeMemoryStore()
    extractor = _extractor(_failing_stream, store)

    added = await extractor.extract_and_store(
        user_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        user_text="u" * 20,
        assistant_text="a" * 20,
    )

    assert added == 0
    assert store.add_calls == []


async def test_extractor_skips_short_exchange_by_min_chars() -> None:
    store = FakeMemoryStore()
    stream, calls = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    extractor = _extractor(stream, store, min_chars=200)

    added = await extractor.extract_and_store(
        user_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        user_text="короткий вопрос",
        assistant_text="короткий ответ",
    )

    assert added == 0
    assert calls == []  # LLM не вызывалась


async def test_extractor_isolates_user_id() -> None:
    owner_id, other_id = uuid.uuid4(), uuid.uuid4()
    foreign = _mem(user_id=other_id, text="Пользователь любит кошек", importance=5)
    store = FakeMemoryStore([foreign])
    stream, _ = _stream_fn([[TextDelta(_VALID_JSON), Done("stop")]])
    extractor = _extractor(stream, store)

    added = await extractor.extract_and_store(
        user_id=owner_id,
        chat_id=uuid.uuid4(),
        user_text="u" * 20,
        assistant_text="a" * 20,
    )

    # чужая запись с тем же текстом не считается дубликатом → оба добавлены
    assert added == 2
    assert store.list_calls == [owner_id]
    assert all(call["user_id"] == owner_id for call in store.add_calls)
    assert store.update_calls == []
    assert foreign.importance == 5  # чужая запись не тронута
