"""Исполнение tool calls: валидация аргументов, права, таймаут, аудит в tool_calls.

execute() никогда не бросает исключений: любой сбой превращается в ToolExecution
со статусом != "ok" и безопасным текстом для модели (детали — только в лог).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.events import ToolCall, ToolResult
from app.llm.tools.registry import ToolContext, ToolDefinition, ToolRegistry, has_permission
from app.llm.tools.schemas import validate_json_schema

logger = logging.getLogger(__name__)

_RESULT_PREVIEW_SIZE = 500
_ARGUMENTS_PREVIEW_SIZE = 4000


class ToolExecutionError(Exception):
    """Контролируемая ошибка хендлера: текст исключения уйдёт модели как есть."""


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """Итог исполнения одного tool call (result.content — текст для модели)."""

    call: ToolCall
    result: ToolResult
    status: str  # ok | error | timeout | denied | invalid_args
    duration_ms: int


class ToolRunner:
    """Выполняет ToolCall через реестр: права → валидация → хендлер с таймаутом."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        *,
        generation_run_id: uuid.UUID | None = None,
    ) -> ToolExecution:
        """Исполнить вызов. Исключения наружу не выходят (кроме отмены задачи)."""
        started = time.perf_counter()

        tool = self._registry.get(call.name)
        if tool is None:
            return await self._finish(
                call, context, "error", f"Unknown tool: {call.name}", started, generation_run_id
            )
        if not tool.enabled:
            return await self._finish(
                call, context, "denied", "Tool is disabled", started, generation_run_id
            )
        if not has_permission(context.permissions, tool.required_permission):
            return await self._finish(
                call, context, "denied", "Tool not allowed", started, generation_run_id
            )

        try:
            args: Any = json.loads(call.arguments_json or "{}")
        except (ValueError, TypeError) as exc:
            return await self._finish(
                call,
                context,
                "invalid_args",
                f"Invalid arguments JSON: {exc}",
                started,
                generation_run_id,
            )
        if not isinstance(args, dict):
            return await self._finish(
                call,
                context,
                "invalid_args",
                "Invalid arguments: expected JSON object",
                started,
                generation_run_id,
            )

        schema_errors = validate_json_schema(tool.parameters, args)
        if schema_errors:
            return await self._finish(
                call,
                context,
                "invalid_args",
                "Invalid arguments: " + "; ".join(schema_errors),
                started,
                generation_run_id,
            )

        status, content = await self._invoke(tool, args, context)
        return await self._finish(call, context, status, content, started, generation_run_id)

    async def _invoke(
        self, tool: ToolDefinition, args: dict[str, Any], context: ToolContext
    ) -> tuple[str, str]:
        """Запуск хендлера с таймаутом → (status, content)."""
        try:
            raw = await asyncio.wait_for(tool.handler(args, context), timeout=tool.timeout)
        except TimeoutError:
            logger.warning("tool %s: timeout after %.1fs", tool.name, tool.timeout)
            return "timeout", f"Tool timed out after {tool.timeout:g}s"
        except ToolExecutionError as exc:
            return "error", str(exc) or "Tool error"
        except Exception:
            logger.exception("tool %s: unexpected error", tool.name)
            return "error", "Tool error"
        content = raw if isinstance(raw, str) else str(raw)
        return "ok", content

    async def _finish(
        self,
        call: ToolCall,
        context: ToolContext,
        status: str,
        content: str,
        started: float,
        generation_run_id: uuid.UUID | None,
    ) -> ToolExecution:
        tool = self._registry.get(call.name)
        max_size = tool.max_result_size if tool is not None else 4000
        if len(content) > max_size:
            content = content[:max_size] + "… [truncated]"
        duration_ms = max(0, int((time.perf_counter() - started) * 1000))
        execution = ToolExecution(
            call=call,
            result=ToolResult(
                call_id=call.id,
                name=call.name,
                content=content,
                is_error=status != "ok",
            ),
            status=status,
            duration_ms=duration_ms,
        )
        logger.info(
            "tool %s → %s (%d ms, user %s)", call.name, status, duration_ms, context.user_id
        )
        await self._record(execution, context, generation_run_id)
        return execution

    async def _record(
        self,
        execution: ToolExecution,
        context: ToolContext,
        generation_run_id: uuid.UUID | None,
    ) -> None:
        """Аудит в tool_calls. Только при generation_run_id; сбои БД гасим в лог."""
        if generation_run_id is None or self._session_factory is None:
            return
        try:
            from app.db.models.tool_call import ToolCallRecord
        except ImportError:
            logger.warning("tool_calls: модель ToolCallRecord недоступна, запись пропущена")
            return
        try:
            async with self._session_factory() as session:
                session.add(
                    ToolCallRecord(
                        generation_run_id=generation_run_id,
                        chat_id=context.chat_id,
                        tool_name=execution.call.name,
                        arguments_json=execution.call.arguments_json[:_ARGUMENTS_PREVIEW_SIZE],
                        status=execution.status,
                        result_preview=execution.result.content[:_RESULT_PREVIEW_SIZE],
                        duration_ms=execution.duration_ms,
                    )
                )
                await session.commit()
        except Exception:
            logger.exception("tool_calls: не удалось записать аудит (%s)", execution.call.name)
