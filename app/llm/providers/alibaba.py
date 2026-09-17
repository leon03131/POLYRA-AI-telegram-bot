"""Alibaba Cloud Model Studio провайдер (OpenAI-compatible Chat Completions, всегда stream).

Wire: POST {base_url}/chat/completions, Authorization: Bearer <key>,
stream=true + stream_options.include_usage=true, SSE-чанки chat.completion.chunk.

Контракты: app/llm/base.py (LLMRequest), app/llm/events.py (LLMEvent),
app/llm/errors.py (ProviderError). Vendor-факты: docs/vendor/ALIBABA.md (2026-09-18).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import LLMRequest
from app.llm.errors import (
    AuthError,
    ErrorCategory,
    NetworkError,
    ProviderError,
    RateLimitError,
    ServerError,
    TimeoutError_,
    classify_http_status,
)
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta, ToolCall, Usage

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = 500  # ошибки обрезаем, чтобы не тащить мегабайты в логи/телегу
_MAX_ATTEMPTS = 2  # 1 исходная попытка + 1 повтор
_BACKOFF_SECONDS = (0.5, 1.0)

_QWEN_MODELS = frozenset({"qwen3.8-flash", "qwen3.8-max"})
_GLM_MODEL = "glm-5.3"

# UI-уровень thinking -> top-level параметры запроса (docs/vendor/ALIBABA.md, маппинг §9.1).
# thinking_budget в проекте не используется; reasoning_effort с ним НИКОГДА не смешиваем.
_THINKING_TABLE: dict[str, dict[str, dict[str, Any]]] = {
    "qwen3.8-flash": {
        "off": {"enable_thinking": False},
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "max": {"reasoning_effort": "xhigh"},
    },
    "qwen3.8-max": {
        "off": {"enable_thinking": False},
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "max": {"reasoning_effort": "xhigh"},
    },
    "deepseek-v4.1-flash": {
        "off": {"enable_thinking": False},
        "low": {"reasoning_effort": "low"},
        "high": {"reasoning_effort": "high"},
        "max": {"reasoning_effort": "max"},
    },
    _GLM_MODEL: {  # thinking-only: "off" registry не выдаёт
        "low": {"reasoning_effort": "low"},
        "high": {"reasoning_effort": "high"},
        "max": {"reasoning_effort": "max"},
    },
    "kimi-k3": {
        "off": {"enable_thinking": False},
        "low": {"reasoning_effort": "low"},
        "high": {"reasoning_effort": "high"},
        "max": {"reasoning_effort": "max"},
    },
}


def _truncate(text: str, limit: int = _MESSAGE_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _thinking_params(model: str, thinking: str | None) -> dict[str, Any]:
    """Top-level thinking-параметры запроса для модели и UI-уровня."""
    params: dict[str, Any] = {}
    if model in _QWEN_MODELS:
        params["preserve_thinking"] = False  # всегда, даже при thinking=None
    if thinking is None:
        return params
    level = _THINKING_TABLE.get(model, {}).get(thinking)
    if level:
        params.update(level)
        if model == _GLM_MODEL:
            params["clear_thinking"] = True
    return params


def _map_assistant_message(parts: list[dict[str, Any]]) -> dict[str, Any]:
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    tool_calls = [
        {
            "id": p.get("id", ""),
            "type": "function",
            "function": {"name": p.get("name", ""), "arguments": p.get("arguments_json", "")},
        }
        for p in parts
        if p.get("type") == "tool_call"
    ]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _map_content_message(role: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    """user/system: parts -> content-массив (text + image_url data URI)."""
    content: list[dict[str, Any]] = []
    for part in parts:
        part_type = part.get("type")
        if part_type == "text":
            content.append({"type": "text", "text": part.get("text", "")})
        elif part_type == "image":
            url = f"data:{part.get('mime_type', 'image/png')};base64,{part.get('data_base64', '')}"
            content.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": role, "content": content}


def _map_messages(request: LLMRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system_prompt is not None:
        messages.append({"role": "system", "content": request.system_prompt})
    for message in request.messages:
        role = message.get("role")
        parts = message.get("parts") or []
        if role == "assistant":
            messages.append(_map_assistant_message(parts))
        elif role == "tool":
            for part in parts:
                if part.get("type") == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.get("call_id", ""),
                            "content": part.get("content", ""),
                        }
                    )
        else:
            messages.append(_map_content_message(role or "user", parts))
    return messages


def build_chat_completions_payload(request: LLMRequest) -> dict[str, Any]:
    """Чистая функция: LLMRequest -> тело POST /chat/completions (всегда stream)."""
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": _map_messages(request),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request.max_output_tokens is not None:
        payload["max_completion_tokens"] = request.max_output_tokens
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    if request.tool_choice is not None:
        payload["tool_choice"] = request.tool_choice
    payload.update(_thinking_params(request.model, request.thinking))
    return payload


def classify_stream_error(error: dict[str, Any]) -> ProviderError:
    """Ошибка внутри SSE-стрима ({"error": {...}}) по vendor code."""
    code = str(error.get("code") or "")
    message = _truncate(str(error.get("message") or ""))
    if code.startswith("Throttling.") or code.startswith("limit_"):
        return RateLimitError(message, raw_code=code)
    if code == "InvalidApiKey":
        return AuthError(message, raw_code=code)
    if code in ("ModelUnavailable", "ServiceUnavailable") or code.startswith("InternalError"):
        return ServerError(message, raw_code=code)
    return ProviderError(ErrorCategory.UNKNOWN, message, raw_code=code)


def classify_http_error(status: int, body: bytes) -> ProviderError:
    """Нестримовый HTTP-ответ об ошибке: {"error": {message, type, param, code}}."""
    message = ""
    raw_code: str | None = None
    try:
        error = json.loads(body).get("error") or {}
        message = str(error.get("message") or "")
        raw_code = error.get("code")
    except (json.JSONDecodeError, AttributeError):
        message = body.decode("utf-8", "replace")
    return classify_http_status(status, _truncate(message), raw_code=raw_code)


def _extract_usage(chunk: dict[str, Any]) -> Usage | None:
    usage = chunk.get("usage")
    if not usage:
        return None
    details = usage.get("completion_tokens_details") or {}
    return Usage(
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def _accumulate_tool_call(
    pending: dict[int, dict[str, Any]], tool_call: dict[str, Any]
) -> None:
    index = tool_call.get("index", 0)
    acc = pending.setdefault(index, {"id": None, "name": "", "args": []})
    if tool_call.get("id"):
        acc["id"] = tool_call["id"]
    function = tool_call.get("function") or {}
    if function.get("name"):
        acc["name"] = function["name"]
    if function.get("arguments"):
        acc["args"].append(function["arguments"])


def _flush_tool_calls(pending: dict[int, dict[str, Any]]) -> list[LLMEvent]:
    events: list[LLMEvent] = [
        ToolCall(
            id=acc["id"] or f"call_{index}",
            name=acc["name"],
            arguments_json="".join(acc["args"]),
        )
        for index, acc in sorted(pending.items())
    ]
    pending.clear()
    events.append(Done("tool_calls"))
    return events


def _events_from_choice(
    choice: dict[str, Any], pending: dict[int, dict[str, Any]]
) -> list[LLMEvent]:
    events: list[LLMEvent] = []
    delta = choice.get("delta") or {}
    reasoning = delta.get("reasoning_content")
    if reasoning:
        events.append(ReasoningDelta(reasoning))  # НИКОГДА не TextDelta
    content = delta.get("content")
    if content:
        events.append(TextDelta(content))
    for tool_call in delta.get("tool_calls") or []:
        _accumulate_tool_call(pending, tool_call)
    finish_reason = choice.get("finish_reason")
    if finish_reason == "tool_calls":
        events.extend(_flush_tool_calls(pending))
    elif finish_reason in ("stop", "length"):
        events.append(Done(finish_reason))
    return events


async def parse_chat_completions_sse(lines: AsyncIterator[str]) -> AsyncIterator[LLMEvent]:
    """SSE-строки -> LLMEvent. tool_calls накапливаются по index и эмитятся
    при finish_reason="tool_calls" (id/name — из первого чанка вызова)."""
    pending_tool_calls: dict[int, dict[str, Any]] = {}
    async for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("alibaba: skip non-JSON data line: %r", _truncate(data, 200))
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            raise classify_stream_error(chunk["error"])
        usage = _extract_usage(chunk)
        if usage is not None:
            yield usage
        for choice in chunk.get("choices") or []:
            for event in _events_from_choice(choice, pending_tool_calls):
                yield event


class AlibabaProvider:
    """LLMProvider для Alibaba Model Studio (dashscope compatible-mode)."""

    provider_id = "alibaba"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url,
            trust_env=False,  # ADR-004: игнорируем системные прокси
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        """Закрыть клиент, только если он создан провайдером (внешний не трогаем)."""
        if self._owns_client:
            await self._client.aclose()

    async def stream_chat(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        """Стрим событий. Retry (1 повтор, backoff 0.5s) — только для network/timeout/
        429/5xx и только пока не эмитнуто ни одного события."""
        payload = build_chat_completions_payload(request)
        attempt = 0
        while True:
            events_started = False
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise classify_http_error(response.status_code, body)
                    lines = self._cancellable_lines(response, request)
                    async for event in parse_chat_completions_sse(lines):
                        events_started = True
                        yield event
                return
            except httpx.TimeoutException as exc:
                error: ProviderError = TimeoutError_(f"alibaba timeout ({exc.__class__.__name__})")
            except httpx.TransportError as exc:
                error = NetworkError(f"alibaba network error ({exc.__class__.__name__})")
            except (RateLimitError, ServerError) as exc:
                error = exc
            if events_started or attempt + 1 >= _MAX_ATTEMPTS:
                raise error
            logger.warning(
                "alibaba: retryable error (attempt %d), backoff %.1fs: %s",
                attempt + 1,
                _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)],
                error,
            )
            await asyncio.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
            attempt += 1

    @staticmethod
    async def _cancellable_lines(
        response: httpx.Response, request: LLMRequest
    ) -> AsyncIterator[str]:
        """Строки SSE с проверкой cancellation на каждой: отмена -> закрыть ответ
        и завершить генератор без Done."""
        async for line in response.aiter_lines():
            if request.cancellation.is_set():
                await response.aclose()
                return
            yield line
