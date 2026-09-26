"""web5: веб-чат Mini App — история сообщений, SSE-генерация, Stop.

Три эндпоинта поверх общих сервисов (ChatService/MessageRepository/
GenerationService) — различается только доставка: SSE-очередь WebChannel
вместо Telegram-драфтов. Права те же EffectivePermissions из сессии
(get_current_user повторяет расчёт AccessMiddleware бота: статус/grant/
allowed_models → evaluate_access), инвариант «одна активная генерация на
чат» общий для обеих поверхностей.

SSE-кадр: «event: <name>\\ndata: <json>\\n\\n» (UTF-8). События generate:
delta/error идут из очереди как есть; финальные done/cancelled шлёт
эндпоинт после персистенса (сравнение id последнего assistant-сообщения
до/после генерации). Отключение клиента НЕ отменяет генерацию — задача
живёт независимо и сохраняет ответ, как в Telegram.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUserDep, SessionDep
from app.db.models import Chat, Message, User
from app.db.repositories import MessageRepository
from app.services.chats import ChatService
from app.services.generation import WebChannel

router = APIRouter()

logger = logging.getLogger(__name__)

_MAX_LIMIT = 100
_MAX_TEXT_LENGTH = 20000


class WebMessageRequest(BaseModel):
    """Тело POST /api/chats/{chat_id}/messages (web5): текст сообщения."""

    text: str


def _web_chat_service(request: Request) -> Any:
    """generation_service из app.state; 503, если web-чат не подключён.

    create_app регистрирует роуты web5 всегда; без сервиса (например, вырезан
    конфигурацией) они единообразно отвечают 503 «web chat disabled»."""
    service = getattr(request.app.state, "generation_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="web chat disabled")
    return service


async def _get_own_chat(session: AsyncSession, user: User, chat_id: uuid.UUID) -> Chat:
    """Чат с проверкой владельца (архивный допустим); 404 без раскрытия существования."""
    chat = await ChatService(session).get_chat_for_user(user.id, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


def _message_text(message: Message) -> str:
    """Склеенный текст text-parts сообщения (любая роль; образец extract_user_text)."""
    return " ".join(
        str(part.text or "") for part in message.parts if part.type == "text"
    ).strip()


def _message_out(message: Message) -> dict[str, Any]:
    """Сообщение для Mini App (web5): плоская карточка с text и флагом изображения."""
    return {
        "id": str(message.id),
        "role": message.role,
        "status": message.status,
        "created_at": message.created_at.isoformat(),
        "model_id": message.model_id,
        "text": _message_text(message),
        "has_image": any(part.type == "image" for part in message.parts),
    }


def _sse_frame(event: str, data: dict[str, Any]) -> bytes:
    """SSE-кадр «event: <name>\\ndata: <json>\\n\\n» в UTF-8."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _sentinel_callback(
    queue: asyncio.Queue[Any],
) -> Callable[[asyncio.Task[None]], None]:
    """Done-callback задачи генерации: sentinel None в очередь ПОСЛЕ её завершения.

    Стрим узнаёт о конце генерации только когда ответ уже персистентен —
    тогда финальное done/cancelled можно искать в БД. put_nowait безопасен:
    очередь неограничена."""

    def _on_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error("web5 generation task failed", exc_info=exc)
        queue.put_nowait(None)

    return _on_done


async def _stream_events(
    *,
    queue: asyncio.Queue[Any],
    task: asyncio.Task[None],
    session: AsyncSession,
    chat_id: uuid.UUID,
    model_hint: str | None,
    before_id: uuid.UUID | None,
) -> AsyncIterator[bytes]:
    """SSE-события web5: meta → дельты/ошибки из очереди → done/cancelled.

    Отключение клиента (GeneratorExit/CancelledError на queue.get) не отменяет
    генерацию: задача продолжает работать и персистить ответ; стрим просто
    закрывается. Финальное событие — по новому assistant-сообщению (его id
    отличается от before_id); нового сообщения нет (было error) — тишина."""
    yield _sse_frame("meta", {"chat_id": str(chat_id), "model_hint": model_hint})
    while True:
        item = await queue.get()
        if item is None:
            break  # sentinel из done-callback: генерация завершена
        yield _sse_frame(str(item.get("event", "")), item.get("data") or {})
    # Sentinel ставится done-callback'ом ПОСЛЕ финализации задачи, но ждём её
    # явно (контракт web5): к этому моменту ответ гарантированно персистентен.
    if not task.cancelled():
        with contextlib.suppress(Exception):
            await task  # упавшая задача уже залогирована done-callback'ом
    last = await MessageRepository(session).get_last(chat_id, role="assistant")
    if last is not None and last.id != before_id:
        if last.status == "done":
            yield _sse_frame("done", {"message_id": str(last.id), "status": "done"})
        elif last.status == "cancelled":
            yield _sse_frame(
                "cancelled", {"message_id": str(last.id), "status": "cancelled"}
            )
    await session.rollback()  # финальное чтение открыло транзакцию — отпустить


def _stop_active_generation(service: Any, chat_id: uuid.UUID) -> bool:
    """Остановить активную генерацию чата через сервис генерации.

    web5 (временный компромисс): публичного accessor'а у GenerationService
    пока нет — сначала пробуем появившийся stop_chat (задача lead'а),
    иначе реестр _generations.stop_for_chat. Когда lead добавит
    GenerationService.stop_chat(), обёртка начнёт использовать его сама."""
    stop_chat = getattr(service, "stop_chat", None)
    if callable(stop_chat):
        return bool(stop_chat(chat_id))
    registry = getattr(service, "_generations", None)
    if registry is None:
        return False
    return bool(registry.stop_for_chat(chat_id))


@router.get("/chats/{chat_id}/messages")
async def list_messages(
    chat_id: uuid.UUID,
    request: Request,
    current: CurrentUserDep,
    session: SessionDep,
    limit: int = Query(default=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Страница истории чата (web5): сообщения ASC, offset от старейшего.

    Архивный чат читается (как GET /api/chats/{id}); чужой/удалённый — 404."""
    user, _ = current
    _web_chat_service(request)  # 503, если web-чат не подключён
    chat = await _get_own_chat(session, user, chat_id)
    limit = max(1, min(limit, _MAX_LIMIT))
    repo = MessageRepository(session)
    messages = await repo.list_page(chat.id, limit=limit, offset=offset)
    total = await repo.count_for_chat(chat.id)
    return {"messages": [_message_out(message) for message in messages], "total": total}


@router.post("/chats/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: WebMessageRequest,
    request: Request,
    current: CurrentUserDep,
    session: SessionDep,
) -> StreamingResponse:
    """Отправить сообщение и стримить ответ модели (web5, SSE).

    Проверки ДО старта стрима — обычным JSON: 503 (web-чат не подключён),
    400 (пустой/слишком длинный text), 404 (чужой чат), 403/401 — из
    CurrentUserDep (тот же расчёт прав, что AccessMiddleware бота).

    Генерация стартует отдельной задачей (не отменяется отключением клиента
    — ответ сохранится, как в Telegram); события дельт/ошибок идут из
    очереди WebChannel, финал — done/cancelled после персистенса."""
    user, permissions = current
    service = _web_chat_service(request)
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        raise HTTPException(status_code=503, detail="web chat disabled")

    text = body.text
    if not text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    if len(text) > _MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"text too long (max {_MAX_TEXT_LENGTH} chars)"
        )

    chat = await _get_own_chat(session, user, chat_id)
    before = await MessageRepository(session).get_last(chat.id, role="assistant")
    before_id = before.id if before is not None else None
    # Отпустить соединение БД на время стрима: генерация живёт в своей задаче
    # со своими сессиями (как AccessMiddleware бота), стрим — минуты.
    await session.rollback()

    queue: asyncio.Queue[Any] = asyncio.Queue()
    channel = WebChannel(queue=queue, bot=bot)
    task = asyncio.create_task(
        service.generate(
            bot=bot,
            tg_chat_id=user.telegram_user_id,
            user=user,
            permissions=permissions,
            current_parts=[{"type": "text", "text": text}],
            channel=channel,
            chat_id=chat.id,
        ),
        name=f"web5-generate-{chat.id}",
    )
    task.add_done_callback(_sentinel_callback(queue))
    return StreamingResponse(
        _stream_events(
            queue=queue,
            task=task,
            session=session,
            chat_id=chat.id,
            model_hint=chat.model_id,
            before_id=before_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/chats/{chat_id}/stop")
async def stop_generation(
    chat_id: uuid.UUID,
    request: Request,
    current: CurrentUserDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Остановить активную генерацию чата (web5; общий инвариант с Telegram).

    Активной генерации нет → {"stopped": false} (идемпотентно)."""
    user, _ = current
    service = _web_chat_service(request)
    chat = await _get_own_chat(session, user, chat_id)
    return {"stopped": _stop_active_generation(service, chat.id)}
