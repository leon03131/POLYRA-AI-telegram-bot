"""web5: unit-тесты веб-чата Mini App (app/api/routes/chat_web.py) без БД.

Фейковый generation_service кладёт события в channel.queue (WebChannel из
generation.py); ChatService/MessageRepository подменяются monkeypatch'ем по
образцу test_api.py. SSE проверяется целиком (meta → delta/error →
done/cancelled), включая инвариант отключения клиента: задача генерации НЕ
отменяется и завершается персистентно.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import create_app
from app.api.dependencies import get_current_user, get_db_session
from app.api.routes import chat_web
from app.config import Settings
from app.db.models import User
from app.llm.registry import default_registry
from app.security.crypto import CryptoBox
from app.services.access import GrantView, evaluate_access
from app.services.generation import WebChannel

BOT_TOKEN = "123:abc"
MASTER_KEY = "unit-test-master-key"
OWNER_TG_ID = 795063564


class _BrokenSessionFactory:
    """Фабрика, падающая при открытии сессии: unit-тесты без PostgreSQL."""

    def __call__(self) -> object:
        raise RuntimeError("no database in unit tests")


class _FakeSession:
    """Сессия-заглушка для web5: rollback (отпуск соединения) и commit — no-op."""

    async def rollback(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def _make_web_app(
    *, generation_service: Any | None = None, bot: Any | None = None
) -> FastAPI:
    """Приложение с web-чатом: сервис/бот — фейки (или None для 503-сценариев)."""
    settings = Settings(
        _env_file=None,  # hermetic: не читать реальный .env
        bot_token=BOT_TOKEN,
        owner_telegram_id=OWNER_TG_ID,
        master_encryption_key=MASTER_KEY,
        miniapp_dist="nonexistent-miniapp-dist",
    )
    return create_app(
        settings=settings,
        session_factory=_BrokenSessionFactory(),
        crypto=CryptoBox(MASTER_KEY),
        registry=default_registry(),
        generation_service=generation_service,
        bot=bot,
    )


def _client(app: FastAPI) -> AsyncClient:
    """ASGI-клиент; 500 отдаются как ответ, а не исключение (raise_app_exceptions=False)."""
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _make_user(*, is_owner: bool = True) -> User:
    user = User(
        telegram_user_id=OWNER_TG_ID if is_owner else 111,
        username="fake",
        first_name="Fake",
    )
    user.status = "active"
    return user


def _make_permissions(is_owner: bool = True) -> Any:
    grant = GrantView(
        status="active",
        expires_at=None,
        requests_per_day=100,
        token_limit=50000,
        max_concurrent_generations=3,
        can_use_web_search=True,
        can_use_memory=True,
    )
    return evaluate_access(
        is_owner=is_owner,
        user_status="active",
        grant=grant,
        allowed_models=None,
        now=datetime.now(UTC),
    )


def _override_user(app: FastAPI, *, user: User | None = None) -> User:
    """Подменить get_current_user; возвращает подставного пользователя."""
    if user is None:
        user = _make_user()
    is_owner = user.telegram_user_id == OWNER_TG_ID
    app.dependency_overrides[get_current_user] = lambda: (user, _make_permissions(is_owner))
    return user


def _override_db(app: FastAPI) -> None:
    """Подменить get_db_session фейковой сессией (rollback/commit — no-op)."""

    async def _fake_session() -> Any:
        yield _FakeSession()

    app.dependency_overrides[get_db_session] = _fake_session


# --- фейки предметного слоя -------------------------------------------------------


def _fake_chat(owner_user_id: uuid.UUID, *, model_id: str | None = None) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), owner_user_id=owner_user_id, model_id=model_id, archived_at=None
    )


def _fake_message(
    *,
    role: str = "user",
    status: str = "done",
    text: str | None = None,
    parts: list[Any] | None = None,
    model_id: str | None = None,
) -> Any:
    if parts is None:
        parts = [SimpleNamespace(type="text", text=text or "")]
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        status=status,
        created_at=datetime.now(UTC),
        model_id=model_id,
        parts=parts,
    )


class _FakeChatService:
    """Подмена ChatService.get_chat_for_user: владелец видит только свой чат."""

    def __init__(self, session: Any, chat: Any | None) -> None:
        self._chat = chat

    async def get_chat_for_user(self, user_id: uuid.UUID, chat_id: uuid.UUID) -> Any | None:
        chat = self._chat
        if (
            chat is not None
            and chat.id == chat_id
            and chat.owner_user_id == user_id
        ):
            return chat
        return None


class _FakeMessageRepository:
    """Подмена MessageRepository: статичный список сообщений (web5).

    Персистенс финального ответа имитируется дописыванием в тот же список
    (on_finish фейкового сервиса генерации)."""

    def __init__(self, session: Any, messages: list[Any]) -> None:
        self._messages = messages
        self.page_calls: list[dict[str, Any]] = []

    async def list_page(self, chat_id: uuid.UUID, *, limit: int, offset: int) -> list[Any]:
        self.page_calls.append({"limit": limit, "offset": offset})
        return self._messages[offset : offset + limit]

    async def count_for_chat(self, chat_id: uuid.UUID) -> int:
        return len(self._messages)

    async def get_last(self, chat_id: uuid.UUID, *, role: str | None = None) -> Any | None:
        if role == "assistant":
            for message in reversed(self._messages):
                if message.role == "assistant":
                    return message
            return None
        return self._messages[-1] if self._messages else None


class _FakeGenerationService:
    """Фейковый GenerationService: generate() кладёт события в channel.queue.

    on_finish вызывается после всех событий — имитация персистенса ответа."""

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        on_finish: Any | None = None,
    ) -> None:
        self.events = events or []
        self.on_finish = on_finish
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        channel = kwargs["channel"]
        for event in self.events:
            await channel.queue.put(event)
        if self.on_finish is not None:
            self.on_finish()


def _patch_chat_web(
    monkeypatch: pytest.MonkeyPatch, *, chat: Any | None, messages: list[Any]
) -> _FakeMessageRepository:
    """Подменить ChatService/MessageRepository в chat_web (без БД)."""
    repo = _FakeMessageRepository(None, messages)
    monkeypatch.setattr(
        chat_web, "ChatService", lambda session: _FakeChatService(session, chat)
    )
    monkeypatch.setattr(chat_web, "MessageRepository", lambda session: repo)
    return repo


# --- 503: web-чат не подключён -----------------------------------------------------


@pytest.mark.parametrize(
    "method",
    ["GET", "POST"],
)
async def test_web_routes_503_without_generation_service(method: str) -> None:
    """Без generation_service все роуты web5 отвечают 503 «web chat disabled»."""
    chat_id = uuid.uuid4()
    app = _make_web_app()
    _override_user(app)
    _override_db(app)
    async with _client(app) as client:
        response = await client.request(
            method, f"/api/chats/{chat_id}/messages", json={"text": "hi"}
        )
        stop = await client.post(f"/api/chats/{chat_id}/stop")
    assert response.status_code == 503
    assert response.json() == {"detail": "web chat disabled"}
    assert stop.status_code == 503
    assert stop.json() == {"detail": "web chat disabled"}


async def test_web_post_503_without_bot() -> None:
    """Сервис есть, бота нет — POST-генерация невозможна: 503, не 500."""
    chat_id = uuid.uuid4()
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=None)
    _override_user(app)
    _override_db(app)
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat_id}/messages", json={"text": "hi"})
    assert response.status_code == 503
    assert response.json() == {"detail": "web chat disabled"}


async def test_web_get_messages_requires_authorization_401() -> None:
    """Без Bearer-токена — 401 до всяких проверок web-чата."""
    app = _make_web_app(
        generation_service=_FakeGenerationService(), bot=SimpleNamespace()
    )
    async with _client(app) as client:
        response = await client.get(f"/api/chats/{uuid.uuid4()}/messages")
    assert response.status_code == 401


# --- GET /api/chats/{chat_id}/messages ---------------------------------------------


async def test_web_get_messages_pagination_and_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Страница ASC от старейшего: total/offset, склейка text-parts, has_image."""
    user = _make_user()
    chat = _fake_chat(user.id)
    messages = [
        _fake_message(role="user", text="первый"),
        _fake_message(role="assistant", text="второй", model_id="kimi-k3"),
        _fake_message(
            role="user",
            parts=[
                SimpleNamespace(type="text", text="част"),
                SimpleNamespace(type="text", text="ями"),
            ],
        ),
        _fake_message(
            role="assistant",
            status="cancelled",
            parts=[
                SimpleNamespace(type="image", text=None),
                SimpleNamespace(type="text", text="фото"),
            ],
            model_id="glm-5.3",
        ),
        _fake_message(role="user", text="пятый"),
    ]
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    repo = _patch_chat_web(monkeypatch, chat=chat, messages=messages)
    async with _client(app) as client:
        response = await client.get(f"/api/chats/{chat.id}/messages?limit=2&offset=1")
        full = await client.get(f"/api/chats/{chat.id}/messages?limit=500")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert [m["id"] for m in body["messages"]] == [str(messages[1].id), str(messages[2].id)]
    assert body["messages"][0]["model_id"] == "kimi-k3"
    assert body["messages"][1]["text"] == "част ями"  # склейка text-parts
    assert body["messages"][1]["has_image"] is False
    # полная страница: has_image у image-part, статус/created_at присутствуют
    assert full.status_code == 200
    items = full.json()["messages"]
    assert len(items) == 5
    assert items[3]["has_image"] is True
    assert items[3]["status"] == "cancelled"
    assert items[3]["text"] == "фото"
    assert set(items[0]) == {
        "id",
        "role",
        "status",
        "created_at",
        "model_id",
        "text",
        "has_image",
    }
    assert items[0]["created_at"] == messages[0].created_at.isoformat()
    # limit=500 зажат до 100 (по образцу chats.py)
    assert repo.page_calls[-1] == {"limit": 100, "offset": 0}


async def test_web_get_messages_foreign_chat_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Чужой/несуществующий чат — 404 без раскрытия существования."""
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=SimpleNamespace())
    _override_user(app)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=None, messages=[])
    async with _client(app) as client:
        response = await client.get(f"/api/chats/{uuid.uuid4()}/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "chat not found"}


# --- POST /api/chats/{chat_id}/messages (SSE) --------------------------------------


async def test_web_post_message_sse_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE-стрим целиком: meta → delta×2 → error → done с message_id персистенса."""
    user = _make_user()
    chat = _fake_chat(user.id, model_id="glm-5.3")
    old_assistant = _fake_message(role="assistant", text="старый ответ", model_id="kimi-k3")
    final = _fake_message(
        role="assistant", status="done", text="итоговый ответ", model_id="glm-5.3"
    )
    messages: list[Any] = [old_assistant]
    service = _FakeGenerationService(
        events=[
            {"event": "delta", "data": {"text": "Привет"}},
            {"event": "delta", "data": {"text": ", мир"}},
            {"event": "error", "data": {"message": "лимит"}},
        ],
        on_finish=lambda: messages.append(final),
    )
    fake_bot = SimpleNamespace(name="fake-bot")
    app = _make_web_app(generation_service=service, bot=fake_bot)
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=messages)

    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/messages", json={"text": "вопрос"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert (
        f'event: meta\ndata: {{"chat_id": "{chat.id}", "model_hint": "glm-5.3"}}\n\n' in body
    )
    assert body.count("event: delta") == 2
    assert 'data: {"text": "Привет"}' in body
    assert 'data: {"text": ", мир"}' in body
    assert 'event: error\ndata: {"message": "лимит"}\n\n' in body
    assert f'event: done\ndata: {{"message_id": "{final.id}", "status": "done"}}\n\n' in body
    assert "event: cancelled" not in body
    # порядок: meta раньше дельт, done — последним
    assert body.index("event: meta") < body.index("event: delta") < body.index("event: done")

    # контракт generate() (web5): канал, чат, права, части сообщения, bot
    assert len(service.calls) == 1
    kwargs = service.calls[0]
    assert kwargs["tg_chat_id"] == user.telegram_user_id
    assert kwargs["chat_id"] == chat.id
    assert kwargs["current_parts"] == [{"type": "text", "text": "вопрос"}]
    assert isinstance(kwargs["channel"], WebChannel)
    assert kwargs["channel"].queue is not None
    assert kwargs["bot"] is fake_bot
    assert kwargs["user"] is user
    assert kwargs["permissions"].allowed is True


async def test_web_post_message_sse_cancelled_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отменённая генерация: финальное событие cancelled с message_id."""
    user = _make_user()
    chat = _fake_chat(user.id)
    final = _fake_message(role="assistant", status="cancelled", text="частичный ответ")
    messages: list[Any] = []
    service = _FakeGenerationService(
        events=[{"event": "delta", "data": {"text": "част"}}],
        on_finish=lambda: messages.append(final),
    )
    app = _make_web_app(generation_service=service, bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=messages)
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/messages", json={"text": "стоп"})
    assert response.status_code == 200
    body = response.text
    assert 'event: delta\ndata: {"text": "част"}\n\n' in body
    assert (
        f'event: cancelled\ndata: {{"message_id": "{final.id}", "status": "cancelled"}}\n\n'
        in body
    )
    assert "event: done" not in body


async def test_web_post_message_generate_crash_no_final_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate() упал: SSE не рвётся, финального done нет (было только delta)."""

    class _CrashingService:
        async def generate(self, **kwargs: Any) -> None:
            channel = kwargs["channel"]
            await channel.queue.put({"event": "delta", "data": {"text": "част"}})
            raise RuntimeError("boom")

    user = _make_user()
    chat = _fake_chat(user.id)
    old_assistant = _fake_message(role="assistant", text="старый")
    messages: list[Any] = [old_assistant]
    app = _make_web_app(generation_service=_CrashingService(), bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=messages)
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/messages", json={"text": "вопрос"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: meta" in body
    assert 'event: delta\ndata: {"text": "част"}\n\n' in body
    assert "event: done" not in body
    assert "event: cancelled" not in body


async def test_web_post_message_busy_error_via_channel_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ внутри generate() (занят/лимит/модель) → WebChannel.notify → error, без финала."""

    class _BusyService:
        async def generate(self, **kwargs: Any) -> None:
            await kwargs["channel"].notify("⏳ Дождитесь завершения текущего ответа.")

    user = _make_user()
    chat = _fake_chat(user.id)
    messages: list[Any] = []
    app = _make_web_app(generation_service=_BusyService(), bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=messages)
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/messages", json={"text": "вопрос"})
    assert response.status_code == 200
    body = response.text
    assert "event: meta" in body
    assert (
        'event: error\ndata: {"message": "⏳ Дождитесь завершения текущего ответа."}\n\n'
        in body
    )
    assert "event: delta" not in body
    assert "event: done" not in body


@pytest.mark.parametrize("text", ["", "   \n\t "])
async def test_web_post_message_empty_text_400(text: str) -> None:
    """Пустой/пробельный text → 400 обычным JSON (не SSE)."""
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=SimpleNamespace())
    _override_user(app)
    _override_db(app)
    async with _client(app) as client:
        response = await client.post(
            f"/api/chats/{uuid.uuid4()}/messages", json={"text": text}
        )
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


async def test_web_post_message_too_long_text_400() -> None:
    """text длиннее 20000 символов → 400."""
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=SimpleNamespace())
    _override_user(app)
    _override_db(app)
    async with _client(app) as client:
        response = await client.post(
            f"/api/chats/{uuid.uuid4()}/messages", json={"text": "а" * 20001}
        )
    assert response.status_code == 400
    assert "too long" in response.json()["detail"]


async def test_web_post_message_missing_text_422() -> None:
    """Тело без поля text → 422 (pydantic)."""
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=SimpleNamespace())
    _override_user(app)
    _override_db(app)
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{uuid.uuid4()}/messages", json={})
    assert response.status_code == 422


async def test_web_post_message_foreign_chat_404_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Чужой чат — 404 ДО старта стрима, обычным JSON (не SSE)."""
    app = _make_web_app(generation_service=_FakeGenerationService(), bot=SimpleNamespace())
    _override_user(app)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=None, messages=[])
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{uuid.uuid4()}/messages", json={"text": "x"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


# --- отключение клиента: генерация не отменяется -----------------------------------


async def test_web_sse_disconnect_does_not_cancel_generation() -> None:
    """GeneratorExit (закрытие стрима клиентом) не отменяет задачу генерации."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    completed = asyncio.Event()

    async def _fake_generate() -> None:
        await queue.put({"event": "delta", "data": {"text": "chunk"}})
        await asyncio.sleep(0.05)  # «долгая» генерация переживает отключение
        completed.set()

    task = asyncio.create_task(_fake_generate())
    task.add_done_callback(chat_web._sentinel_callback(queue))
    stream = chat_web._stream_events(
        queue=queue,
        task=task,
        session=_FakeSession(),
        chat_id=uuid.uuid4(),
        model_hint=None,
        before_id=None,
    )
    assert (await stream.__anext__()).startswith(b"event: meta")
    assert (await stream.__anext__()).startswith(b"event: delta")
    await stream.aclose()  # клиент закрыл соединение
    assert not task.cancelled()
    await task  # генерация дошла до конца и «персистилась»
    assert completed.is_set()


async def test_web_sse_cancellation_does_not_cancel_generation() -> None:
    """CancelledError в стриме (Starlette отменяет стрим-таску) не отменяет генерацию."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    completed = asyncio.Event()

    async def _fake_generate() -> None:
        await queue.put({"event": "delta", "data": {"text": "chunk"}})
        await asyncio.sleep(0.05)
        completed.set()

    task = asyncio.create_task(_fake_generate())
    task.add_done_callback(chat_web._sentinel_callback(queue))
    stream = chat_web._stream_events(
        queue=queue,
        task=task,
        session=_FakeSession(),
        chat_id=uuid.uuid4(),
        model_hint=None,
        before_id=None,
    )
    assert (await stream.__anext__()).startswith(b"event: meta")
    assert (await stream.__anext__()).startswith(b"event: delta")
    with pytest.raises(asyncio.CancelledError):
        await stream.athrow(asyncio.CancelledError)
    assert not task.cancelled()
    await task
    assert completed.is_set()


# --- POST /api/chats/{chat_id}/stop ------------------------------------------------


async def test_web_stop_active_generation_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Активная генерация чата есть → stop_for_chat → {"stopped": true}."""
    user = _make_user()
    chat = _fake_chat(user.id)
    registry = SimpleNamespace(stop_for_chat=lambda chat_id: chat_id == chat.id)
    service = SimpleNamespace(_generations=registry)
    app = _make_web_app(generation_service=service, bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=[])
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": True}


async def test_web_stop_no_active_generation_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Активной генерации нет → {"stopped": false} (идемпотентно)."""
    user = _make_user()
    chat = _fake_chat(user.id)
    registry = SimpleNamespace(stop_for_chat=lambda chat_id: False)
    service = SimpleNamespace(_generations=registry)
    app = _make_web_app(generation_service=service, bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=[])
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": False}


async def test_web_stop_prefers_public_stop_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Появился публичный GenerationService.stop_chat (задача lead'а) — используется он."""
    user = _make_user()
    chat = _fake_chat(user.id)
    calls: list[uuid.UUID] = []

    def _stop_chat(chat_id: uuid.UUID) -> bool:
        calls.append(chat_id)
        return True

    service = SimpleNamespace(
        stop_chat=_stop_chat,
        _generations=SimpleNamespace(stop_for_chat=lambda chat_id: False),
    )
    app = _make_web_app(generation_service=service, bot=SimpleNamespace())
    _override_user(app, user=user)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=chat, messages=[])
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{chat.id}/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": True}
    assert calls == [chat.id]


async def test_web_stop_foreign_chat_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Чужой чат — стоп невозможен: 404."""
    app = _make_web_app(
        generation_service=SimpleNamespace(
            _generations=SimpleNamespace(stop_for_chat=lambda chat_id: True)
        ),
        bot=SimpleNamespace(),
    )
    _override_user(app)
    _override_db(app)
    _patch_chat_web(monkeypatch, chat=None, messages=[])
    async with _client(app) as client:
        response = await client.post(f"/api/chats/{uuid.uuid4()}/stop")
    assert response.status_code == 404
