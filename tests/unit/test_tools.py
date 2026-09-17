"""Unit-тесты tool engine (app.llm.tools).

БД нет: session_factory — фейк с async context manager; ChatRepository /
MemoryRepository в builtin monkeypatch'ятся; app.search подменяется фейковыми
модулями в sys.modules (хендлеры импортируют их лениво).
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import Settings
from app.llm.events import ToolCall
from app.llm.tools import builtin
from app.llm.tools.builtin import build_default_registry
from app.llm.tools.registry import ToolContext, ToolDefinition, ToolRegistry
from app.llm.tools.runner import ToolExecutionError, ToolRunner
from app.llm.tools.schemas import make_llm_tools, validate_json_schema
from app.memory.normalizer import normalize_memory_text
from app.services.access import EffectivePermissions

# --- Фейки -----------------------------------------------------------------------


def _perms(*, web: bool = True, memory: bool = True) -> EffectivePermissions:
    return EffectivePermissions(
        allowed=True,
        reason="ok",
        allowed_models=None,
        can_use_web_search=web,
        can_use_memory=memory,
        requests_per_day=None,
        token_limit=None,
        max_concurrent_generations=1,
    )


class FakeSession:
    """Минимальный фейк AsyncSession для аудита tool_calls."""

    def __init__(self, *, fail_commit: bool = False) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.fail_commit = fail_commit

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("db down")

    async def flush(self) -> None:
        pass


class FakeSessionFactory:
    """async_sessionmaker-совместимый фейк: вызов → async CM, отдающий session."""

    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()

    def __call__(self) -> FakeSessionFactory:
        return self

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _context(
    *,
    permissions: EffectivePermissions | None = None,
    session_factory: Any = None,
    search_manager: Any = None,
) -> ToolContext:
    return ToolContext(
        user_id=uuid.uuid4(),
        chat_id=uuid.uuid4(),
        permissions=permissions or _perms(),
        session_factory=session_factory or FakeSessionFactory(),
        settings=Settings(),
        search_manager=search_manager,
    )


async def _echo_handler(args: dict[str, Any], context: ToolContext) -> str:
    return f"echo: {args.get('text')}"


def _tool(**overrides: Any) -> ToolDefinition:
    base: dict[str, Any] = {
        "name": "echo",
        "description": "echo tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "handler": _echo_handler,
    }
    base.update(overrides)
    return ToolDefinition(**base)


def _call(name: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(
        id="c1",
        name=name,
        arguments_json=json.dumps(arguments if arguments is not None else {"text": "hi"}),
    )


def _install_fake_record_model(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Фейк app.db.models.tool_call.ToolCallRecord (записывает kwargs). Возвращает записи."""
    records: list[dict[str, Any]] = []

    class FakeToolCallRecord:
        def __init__(self, **kwargs: Any) -> None:
            records.append(kwargs)

    module = types.ModuleType("app.db.models.tool_call")
    module.ToolCallRecord = FakeToolCallRecord
    monkeypatch.setitem(sys.modules, "app.db.models.tool_call", module)
    return records


def _install_fake_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Фейк пакета app.search с классом SearchOptions (lenient kwargs)."""
    module = types.ModuleType("app.search")
    module.__path__ = []  # пометить как пакет

    class SearchOptions:
        def __init__(
            self,
            max_results: int = 5,
            language: str = "ru",
            country: str = "ru",
            mode: str = "auto",
        ) -> None:
            self.max_results = max_results
            self.language = language
            self.country = country
            self.mode = mode

    module.SearchOptions = SearchOptions
    monkeypatch.setitem(sys.modules, "app.search", module)


def _install_fake_fetcher(
    monkeypatch: pytest.MonkeyPatch, *, page: Any = None, error: BaseException | None = None
) -> None:
    """Фейк app.search.fetcher.fetch_url (пакет app.search тоже подменяется)."""
    package = types.ModuleType("app.search")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "app.search", package)
    fetcher = types.ModuleType("app.search.fetcher")

    async def fetch_url(url: str, reader: Any = None) -> Any:
        if error is not None:
            raise error
        return page

    fetcher.fetch_url = fetch_url
    monkeypatch.setitem(sys.modules, "app.search.fetcher", fetcher)


class FakeSearchManager:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, Any]] = []

    async def search(self, query: str, options: Any) -> Any:
        self.calls.append((query, options))
        return self.outcome


def _patch_chat_repo(monkeypatch: pytest.MonkeyPatch, chat: Any) -> list[tuple[Any, str]]:
    """Подменить ChatRepository в builtin на фейк поверх данного chat."""
    rename_calls: list[tuple[Any, str]] = []

    class Repo:
        def __init__(self, session: Any) -> None:
            pass

        async def get(self, chat_id: uuid.UUID) -> Any:
            return chat

        async def rename(self, chat_id: uuid.UUID, title: str) -> Any:
            rename_calls.append((chat_id, title))
            chat.title = title
            return chat

    monkeypatch.setattr(builtin, "ChatRepository", Repo)
    return rename_calls


def _memory(text: str, *, user_id: uuid.UUID, importance: int = 5) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        text=text,
        normalized_text=normalize_memory_text(text),
        importance=importance,
    )


class FakeMemoryRepository:
    """Фейк MemoryRepository поверх списка store (store задаётся класс-атрибутом)."""

    store: list[Any] = []

    def __init__(self, session: Any) -> None:
        pass

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        return [m for m in self.store if m.user_id == user_id][:limit]

    async def add(self, user_id: uuid.UUID, **fields: Any) -> Any:
        memory = SimpleNamespace(id=uuid.uuid4(), user_id=user_id, **fields)
        self.store.append(memory)
        return memory

    async def update_fields(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, **fields: Any
    ) -> Any | None:
        for memory in self.store:
            if memory.id == memory_id and memory.user_id == user_id:
                for key, value in fields.items():
                    setattr(memory, key, value)
                return memory
        return None

    async def search_fts(self, user_id: uuid.UUID, query: str, *, limit: int = 5) -> list[Any]:
        return [m for m in self.store if m.user_id == user_id and query.lower() in m.text.lower()][
            :limit
        ]

    async def delete(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        for index, memory in enumerate(self.store):
            if memory.id == memory_id and memory.user_id == user_id:
                del self.store[index]
                return True
        return False


def _patch_memory_repo(monkeypatch: pytest.MonkeyPatch, store: list[Any]) -> None:
    """Подменить MemoryRepository в builtin на фейк поверх списка store."""
    FakeMemoryRepository.store = store
    monkeypatch.setattr(builtin, "MemoryRepository", FakeMemoryRepository)


# --- validate_json_schema ---------------------------------------------------------


class TestValidateJsonSchema:
    SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            "mode": {"type": "string", "enum": ["auto", "normal", "ai_overview"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def test_ok(self) -> None:
        errors = validate_json_schema(
            self.SCHEMA, {"query": "q", "max_results": 5, "mode": "auto", "tags": ["a"]}
        )
        assert errors == []

    def test_missing_required(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {})
        assert any("query" in error and "required" in error for error in errors)

    def test_wrong_type(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": "q", "max_results": "5"})
        assert any("max_results" in error and "integer" in error for error in errors)

    def test_bool_is_not_integer(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": "q", "max_results": True})
        assert any("max_results" in error for error in errors)

    def test_enum_violation(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": "q", "mode": "weird"})
        assert any("enum" in error for error in errors)

    def test_additional_properties_rejected(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": "q", "extra": 1})
        assert any("extra" in error and "unexpected" in error for error in errors)

    def test_number_range(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": "q", "max_results": 99})
        assert any("maximum" in error for error in errors)

    def test_min_length(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": ""})
        assert any("minLength" in error for error in errors)

    def test_array_items(self) -> None:
        errors = validate_json_schema(self.SCHEMA, {"query": "q", "tags": ["a", 1]})
        assert any("tags[1]" in error for error in errors)


# --- registry ---------------------------------------------------------------------


class TestRegistry:
    def test_list_enabled_filters_by_permission(self) -> None:
        registry = build_default_registry()
        all_names = {tool.name for tool in registry.list_enabled(_perms(web=True, memory=True))}
        expected = {"web_search", "open_url", "set_chat_title", "remember", "forget_memory"}
        assert all_names == expected
        no_web = {tool.name for tool in registry.list_enabled(_perms(web=False))}
        assert "web_search" not in no_web and "open_url" not in no_web
        assert "set_chat_title" in no_web
        no_memory = {tool.name for tool in registry.list_enabled(_perms(memory=False))}
        assert "remember" not in no_memory and "forget_memory" not in no_memory

    def test_list_enabled_skips_disabled(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool(enabled=False))
        assert registry.list_enabled(_perms()) == []

    def test_get_unknown_returns_none(self) -> None:
        assert ToolRegistry().get("nope") is None

    def test_make_llm_tools(self) -> None:
        definitions = build_default_registry().list_enabled(_perms())
        llm_tools = make_llm_tools(definitions)
        assert {tool.name for tool in llm_tools} == {tool.name for tool in definitions}
        assert all(tool.parameters.get("type") == "object" for tool in llm_tools)


# --- runner -----------------------------------------------------------------------


class TestRunner:
    async def test_unknown_tool(self) -> None:
        runner = ToolRunner(ToolRegistry())
        execution = await runner.execute(
            ToolCall(id="c1", name="nope", arguments_json="{}"), _context()
        )
        assert execution.status == "error"
        assert execution.result.is_error
        assert "Unknown tool" in execution.result.content

    async def test_disabled_tool_denied(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool(enabled=False))
        execution = await ToolRunner(registry).execute(_call("echo"), _context())
        assert execution.status == "denied"
        assert execution.result.is_error

    async def test_permission_denied(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool(required_permission="web_search"))
        execution = await ToolRunner(registry).execute(
            _call("echo"), _context(permissions=_perms(web=False))
        )
        assert execution.status == "denied"
        assert execution.result.content == "Tool not allowed"

    async def test_invalid_json_arguments(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool())
        execution = await ToolRunner(registry).execute(
            ToolCall(id="c1", name="echo", arguments_json="{"), _context()
        )
        assert execution.status == "invalid_args"
        assert execution.result.is_error

    async def test_schema_invalid_arguments(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool())
        execution = await ToolRunner(registry).execute(_call("echo", {}), _context())
        assert execution.status == "invalid_args"
        assert execution.result.content.startswith("Invalid arguments:")

    async def test_timeout(self) -> None:
        async def slow(args: dict[str, Any], context: ToolContext) -> str:
            await asyncio.sleep(0.3)
            return "done"

        registry = ToolRegistry()
        registry.register(_tool(handler=slow, timeout=0.05))
        execution = await ToolRunner(registry).execute(_call("echo"), _context())
        assert execution.status == "timeout"
        assert execution.result.is_error

    async def test_result_truncated_to_max_result_size(self) -> None:
        async def big(args: dict[str, Any], context: ToolContext) -> str:
            return "x" * 100

        registry = ToolRegistry()
        registry.register(_tool(handler=big, max_result_size=10))
        execution = await ToolRunner(registry).execute(_call("echo"), _context())
        assert execution.status == "ok"
        assert execution.result.content == "x" * 10 + "… [truncated]"

    async def test_unexpected_exception_is_sanitized(self) -> None:
        async def boom(args: dict[str, Any], context: ToolContext) -> str:
            raise RuntimeError("secret internals")

        registry = ToolRegistry()
        registry.register(_tool(handler=boom))
        execution = await ToolRunner(registry).execute(_call("echo"), _context())
        assert execution.status == "error"
        assert execution.result.content == "Tool error"

    async def test_tool_execution_error_text_passes_through(self) -> None:
        async def fail(args: dict[str, Any], context: ToolContext) -> str:
            raise ToolExecutionError("контролируемый текст")

        registry = ToolRegistry()
        registry.register(_tool(handler=fail))
        execution = await ToolRunner(registry).execute(_call("echo"), _context())
        assert execution.status == "error"
        assert execution.result.content == "контролируемый текст"


# --- запись tool_calls ------------------------------------------------------------


class TestToolCallRecording:
    async def test_records_execution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _install_fake_record_model(monkeypatch)
        session = FakeSession()
        registry = ToolRegistry()
        registry.register(_tool())
        runner = ToolRunner(registry, session_factory=FakeSessionFactory(session))
        run_id = uuid.uuid4()
        context = _context()
        execution = await runner.execute(_call("echo"), context, generation_run_id=run_id)
        assert execution.status == "ok"
        assert len(records) == 1
        record = records[0]
        assert record["generation_run_id"] == run_id
        assert record["chat_id"] == context.chat_id
        assert record["tool_name"] == "echo"
        assert record["status"] == "ok"
        assert record["duration_ms"] >= 0
        assert "echo: hi" in record["result_preview"]
        assert session.commits == 1

    async def test_no_record_without_generation_run_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = _install_fake_record_model(monkeypatch)
        session = FakeSession()
        registry = ToolRegistry()
        registry.register(_tool())
        runner = ToolRunner(registry, session_factory=FakeSessionFactory(session))
        execution = await runner.execute(_call("echo"), _context())
        assert execution.status == "ok"
        assert records == []
        assert session.commits == 0

    async def test_record_db_failure_does_not_propagate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_record_model(monkeypatch)
        session = FakeSession(fail_commit=True)
        registry = ToolRegistry()
        registry.register(_tool())
        runner = ToolRunner(registry, session_factory=FakeSessionFactory(session))
        execution = await runner.execute(_call("echo"), _context(), generation_run_id=uuid.uuid4())
        assert execution.status == "ok"
        assert execution.result.content == "echo: hi"


# --- builtin: set_chat_title ------------------------------------------------------


class TestSetChatTitle:
    async def test_sets_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chat = SimpleNamespace(title=None)
        rename_calls = _patch_chat_repo(monkeypatch, chat)
        session = FakeSession()
        context = _context(session_factory=FakeSessionFactory(session))
        execution = await ToolRunner(build_default_registry()).execute(
            _call("set_chat_title", {"title": "разговор про погоду сегодня"}), context
        )
        assert execution.status == "ok"
        assert "название установлено" in execution.result.content
        assert rename_calls == [(context.chat_id, chat.title)]
        assert session.commits == 1

    async def test_title_already_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chat = SimpleNamespace(title="уже есть название")
        rename_calls = _patch_chat_repo(monkeypatch, chat)
        session = FakeSession()
        context = _context(session_factory=FakeSessionFactory(session))
        execution = await ToolRunner(build_default_registry()).execute(
            _call("set_chat_title", {"title": "другое название чата"}), context
        )
        assert execution.status == "ok"
        assert "название уже установлено" in execution.result.content
        assert rename_calls == []
        assert session.commits == 0

    async def test_invalid_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chat = SimpleNamespace(title=None)
        rename_calls = _patch_chat_repo(monkeypatch, chat)
        session = FakeSession()
        context = _context(session_factory=FakeSessionFactory(session))
        execution = await ToolRunner(build_default_registry()).execute(
            _call("set_chat_title", {"title": "одно"}), context
        )
        assert execution.status == "ok"
        assert "невалидное название" in execution.result.content
        assert rename_calls == []


# --- builtin: web_search / open_url ------------------------------------------------


class TestWebSearch:
    async def test_not_configured(self) -> None:
        execution = await ToolRunner(build_default_registry()).execute(
            _call("web_search", {"query": "q"}), _context(search_manager=None)
        )
        assert execution.status == "ok"
        assert "не настроен" in execution.result.content

    async def test_formats_results_with_sources(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_search(monkeypatch)
        outcome = SimpleNamespace(
            results=[
                SimpleNamespace(title="T1", url="https://a", snippet="s1"),
                SimpleNamespace(title="T2", url="https://b", snippet="s2"),
            ],
            ai_overview_text="краткий обзор",
        )
        manager = FakeSearchManager(outcome)
        execution = await ToolRunner(build_default_registry()).execute(
            _call("web_search", {"query": "q", "max_results": 2}),
            _context(search_manager=manager),
        )
        content = execution.result.content
        assert execution.status == "ok"
        assert "1. T1\nhttps://a\ns1" in content
        assert "2. T2" in content
        assert "AI Overview (не доверяй без источников): краткий обзор" in content
        assert "SOURCES: https://a | https://b" in content
        query, options = manager.calls[0]
        assert query == "q"
        assert options.max_results == 2
        assert options.language == "ru" and options.country == "ru"

    async def test_empty_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_search(monkeypatch)
        manager = FakeSearchManager(SimpleNamespace(results=[], ai_overview_text=None))
        execution = await ToolRunner(build_default_registry()).execute(
            _call("web_search", {"query": "q"}), _context(search_manager=manager)
        )
        assert execution.result.content == "ничего не найдено"


class TestOpenUrl:
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = SimpleNamespace(url="https://example.com", text="hello page", truncated=False)
        _install_fake_fetcher(monkeypatch, page=page)
        execution = await ToolRunner(build_default_registry()).execute(
            _call("open_url", {"url": "https://example.com"}), _context()
        )
        assert execution.status == "ok"
        assert execution.result.content.startswith("URL: https://example.com")
        assert "hello page" in execution.result.content

    async def test_ssrf_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class SSRFBlockedError(Exception):
            pass

        _install_fake_fetcher(monkeypatch, error=SSRFBlockedError("10.0.0.1 is private"))
        execution = await ToolRunner(build_default_registry()).execute(
            _call("open_url", {"url": "http://10.0.0.1/"}), _context()
        )
        assert execution.status == "error"
        assert execution.result.is_error
        assert execution.result.content == "URL запрещён политикой безопасности"


# --- builtin: remember / forget_memory ---------------------------------------------


class TestRememberForget:
    async def test_remember_adds_new(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store: list[Any] = []
        _patch_memory_repo(monkeypatch, store)
        session = FakeSession()
        context = _context(session_factory=FakeSessionFactory(session))
        execution = await ToolRunner(build_default_registry()).execute(
            _call("remember", {"text": "любит чай", "category": "preference", "importance": 7}),
            context,
        )
        assert execution.status == "ok"
        assert execution.result.content == "запомнено"
        assert len(store) == 1
        assert store[0].user_id == context.user_id
        assert store[0].category == "preference"
        assert session.commits == 1

    async def test_remember_deduplicates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        context = _context()
        store = [_memory("любит чай", user_id=context.user_id, importance=3)]
        _patch_memory_repo(monkeypatch, store)
        execution = await ToolRunner(build_default_registry()).execute(
            _call("remember", {"text": "любит чай!", "importance": 8}), context
        )
        assert execution.status == "ok"
        assert execution.result.content == "обновлено"
        assert len(store) == 1
        assert store[0].importance == 8

    async def test_forget_deletes_best_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        context = _context()
        store = [_memory("любит зелёный чай по утрам", user_id=context.user_id)]
        _patch_memory_repo(monkeypatch, store)
        execution = await ToolRunner(build_default_registry()).execute(
            _call("forget_memory", {"query": "чай"}), context
        )
        assert execution.status == "ok"
        assert execution.result.content.startswith("забыл: ")
        assert store == []

    async def test_forget_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store: list[Any] = []
        _patch_memory_repo(monkeypatch, store)
        execution = await ToolRunner(build_default_registry()).execute(
            _call("forget_memory", {"query": "чай"}), _context()
        )
        assert execution.status == "ok"
        assert execution.result.content == "ничего не найдено"
