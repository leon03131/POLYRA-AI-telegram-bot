"""Raw httpx адаптер Gemini generateContent (SSE-стрим).

Wire: POST {base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse,
заголовок x-goog-api-key. Факты — docs/vendor/GEMINI.md (2026-09-18).

Один вызов = одна попытка: ротация ключей/квоты — НЕ здесь (milestone M4),
ошибки поднимаются наверх как app.llm.errors.ProviderError.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from app.llm.base import LLMRequest, MessageDict
from app.llm.errors import (
    InvalidRequestError,
    NetworkError,
    ProviderError,
    SafetyError,
    TimeoutError_,
    classify_http_status,
)
from app.llm.events import Done, LLMEvent, ReasoningDelta, TextDelta, ToolCall, Usage

logger = logging.getLogger(__name__)

_FINISH_REASON_MAP = {"STOP": "stop", "MAX_TOKENS": "length"}
_SAFETY_FINISH_REASONS = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"})
_TOOL_CHOICE_MODE = {"auto": "AUTO", "none": "NONE"}
_MAX_ERROR_MESSAGE = 500


def _message_to_content(message: MessageDict) -> dict[str, Any]:
    """Нормализованное сообщение → Gemini content {role, parts} (camelCase)."""
    role = "model" if message.get("role") == "assistant" else "user"
    parts: list[dict[str, Any]] = []
    text_buffer: list[str] = []

    def flush_text() -> None:
        if text_buffer:
            parts.append({"text": "\n".join(text_buffer)})
            text_buffer.clear()

    for part in message.get("parts") or []:
        part_type = part.get("type")
        if part_type == "text":
            text_buffer.append(part.get("text", ""))
        elif part_type == "image":
            flush_text()
            parts.append(
                {"inlineData": {"mimeType": part["mime_type"], "data": part["data_base64"]}}
            )
        elif part_type == "tool_call":
            flush_text()
            raw_args = part.get("arguments_json") or ""
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
            wire_part: dict[str, Any] = {"functionCall": {"name": part["name"], "args": args}}
            signature = (part.get("provider_meta") or {}).get("thought_signature")
            if signature:
                wire_part["thoughtSignature"] = signature
            parts.append(wire_part)
        elif part_type == "tool_result":
            flush_text()
            parts.append(
                {
                    "functionResponse": {
                        "name": part["name"],
                        "response": {"result": part.get("content", "")},
                    }
                }
            )
    flush_text()
    return {"role": role, "parts": parts}


# Gemini Function Declarations принимают только подмножество JSON Schema
# (additionalProperties и прочие → 400 "Unknown name"). Чистим рекурсивно.
_GEMINI_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "title",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "propertyOrdering",
        "anyOf",
    }
)


def sanitize_gemini_schema(schema: Any) -> Any:
    """Оставить только поля, которые понимает Gemini Schema (рекурсивно).

    Особый случай: под ключом `properties` лежат произвольные ИМЕНА свойств —
    их нельзя фильтровать allowlist'ом, санитизируются только их подсхемы.
    """
    if isinstance(schema, list):
        return [sanitize_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: sanitize_gemini_schema(sub) for name, sub in value.items()}
        elif key in _GEMINI_SCHEMA_KEYS:
            out[key] = sanitize_gemini_schema(value)
    return out


def build_generate_content_payload(request: LLMRequest) -> dict[str, Any]:
    """LLMRequest → тело generateContent. temperature/top_p/top_k НЕ передаём."""
    payload: dict[str, Any] = {}
    if request.system_prompt:
        payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
    payload["contents"] = [_message_to_content(m) for m in request.messages]

    generation_config: dict[str, Any] = {}
    if request.thinking:
        level = request.thinking.lower()
        if level != "off":
            generation_config["thinkingConfig"] = {"thinkingLevel": level}
    if request.max_output_tokens is not None:
        generation_config["maxOutputTokens"] = request.max_output_tokens
    if generation_config:
        payload["generationConfig"] = generation_config

    if request.tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": sanitize_gemini_schema(tool.parameters),
                    }
                    for tool in request.tools
                ]
            }
        ]
    if request.tool_choice is not None:
        payload["toolConfig"] = {
            "functionCallingConfig": {"mode": _TOOL_CHOICE_MODE[request.tool_choice]}
        }
    return payload


def _events_from_part(part: dict[str, Any]) -> list[LLMEvent]:
    """Один content-part → события (thought → ReasoningDelta, functionCall → ToolCall)."""
    if part.get("thought"):
        thought_text = part.get("text")
        return [ReasoningDelta(text=thought_text)] if thought_text else []
    function_call = part.get("functionCall")
    if function_call:
        provider_meta: dict[str, Any] = {}
        if part.get("thoughtSignature"):
            provider_meta["thought_signature"] = part["thoughtSignature"]
        return [
            ToolCall(
                id=function_call.get("id") or f"call_{uuid4().hex[:12]}",
                name=function_call.get("name", ""),
                arguments_json=json.dumps(function_call.get("args") or {}, ensure_ascii=False),
                provider_meta=provider_meta,
            )
        ]
    text = part.get("text")
    if text:
        return [TextDelta(text=text)]
    # part без text/functionCall (напр. только thoughtSignature) — пропускаем.
    return []


def _finish_reason_event(candidate: dict[str, Any]) -> list[LLMEvent]:
    finish_reason = candidate.get("finishReason")
    if not finish_reason:
        return []
    if finish_reason in _SAFETY_FINISH_REASONS:
        raise SafetyError(f"gemini finishReason={finish_reason}", raw_code=str(finish_reason))
    mapped = _FINISH_REASON_MAP.get(finish_reason)
    return [Done(finish_reason=mapped)] if mapped else []


def _events_from_chunk(chunk: dict[str, Any]) -> list[LLMEvent]:
    """Один GenerateContentResponse-чанк → события. Safety — исключением."""
    prompt_feedback = chunk.get("promptFeedback") or {}
    block_reason = prompt_feedback.get("blockReason")
    if block_reason:
        raise SafetyError(f"gemini blocked prompt: {block_reason}", raw_code=str(block_reason))

    events: list[LLMEvent] = []
    usage_meta = chunk.get("usageMetadata")
    if usage_meta:
        events.append(
            Usage(
                input_tokens=usage_meta.get("promptTokenCount"),
                output_tokens=usage_meta.get("candidatesTokenCount"),
                reasoning_tokens=usage_meta.get("thoughtsTokenCount"),
                total_tokens=usage_meta.get("totalTokenCount"),
            )
        )

    candidates = chunk.get("candidates") or []
    if not candidates:
        return events
    candidate = candidates[0]
    content = candidate.get("content") or {}
    for part in content.get("parts") or []:
        events.extend(_events_from_part(part))
    events.extend(_finish_reason_event(candidate))
    return events


async def parse_generate_content_sse(lines: AsyncIterator[str]) -> AsyncIterator[LLMEvent]:
    """SSE-строки → события. Пустые/не-data/битый JSON — пропускаем, [DONE] — стоп."""
    async for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data:
            continue
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("gemini: skip non-JSON SSE data line")
            continue
        if isinstance(chunk, dict):
            for event in _events_from_chunk(chunk):
                yield event


def _parse_retry_delay(value: Any) -> float | None:
    """RetryInfo.retryDelay формата "3.5s" → float секунд."""
    if isinstance(value, str) and value.endswith("s"):
        try:
            return float(value[:-1])
        except ValueError:
            return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _error_from_response(status: int, body: bytes) -> ProviderError:
    """Тело {"error": {...}} → ProviderError через classify_http_status + retry_after."""
    message = f"gemini http {status}"
    raw_code: str | None = None
    retry_after: float | None = None
    try:
        error = (json.loads(body) or {}).get("error") or {}
        if error.get("message"):
            message = str(error["message"])[:_MAX_ERROR_MESSAGE]
        if error.get("status"):
            raw_code = str(error["status"])
        for detail in error.get("details") or []:
            if isinstance(detail, dict) and "retryDelay" in detail:
                retry_after = _parse_retry_delay(detail["retryDelay"])
    except json.JSONDecodeError:
        pass
    err = classify_http_status(status, message, raw_code=raw_code)
    if retry_after is not None:
        err.retry_after = retry_after
    return err


class GeminiProvider:
    """Gemini generateContent через raw httpx (custom base_url/proxy владельца)."""

    provider_id = "gemini"

    def __init__(self, *, base_url: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            trust_env=False,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
        )

    async def aclose(self) -> None:
        """Закрыть клиент, только если он создан провайдером (внешний не трогаем)."""
        if self._owns_client:
            await self._client.aclose()

    async def stream_chat(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        """Стрим событий. Один вызов = одна попытка; ошибки — ProviderError наверх."""
        api_key = request.metadata.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise InvalidRequestError("missing api_key")
        payload = build_generate_content_payload(request)

        try:
            async with self._client.stream(
                "POST",
                f"/v1beta/models/{request.model}:streamGenerateContent?alt=sse",
                json=payload,
                headers={"x-goog-api-key": api_key},
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise _error_from_response(response.status_code, body)
                async for event in parse_generate_content_sse(
                    self._lines_with_cancellation(response, request)
                ):
                    yield event
        except httpx.TimeoutException as exc:
            raise TimeoutError_(f"gemini timeout: {type(exc).__name__}") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"gemini network error: {type(exc).__name__}") from exc

    async def _lines_with_cancellation(
        self, response: httpx.Response, request: LLMRequest
    ) -> AsyncIterator[str]:
        """Строки SSE; при cancellation — тихое завершение генератора (без Done)."""
        async for line in response.aiter_lines():
            if request.cancellation.is_set():
                return
            yield line
