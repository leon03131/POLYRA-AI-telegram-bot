"""Единая таксономия ошибок провайдеров.

Правила ротации/повторов (ADR-005) опираются на category/retryable:
- invalid_request (400) — запрос сломан, НЕ ротировать ключи, вернуть ошибку;
- auth (401) — credential мёртв: disable + next project (Gemini);
- forbidden (403) — cooldown проекта + next;
- rate_limit (429) — cooldown project+model + next; retryable;
- server (5xx), network, timeout — bounded retry, затем next; retryable;
- safety — не ротировать пул, вернуть пользователю;
- unknown — не retryable по умолчанию.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTH = "auth"
    FORBIDDEN = "forbidden"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    NETWORK = "network"
    TIMEOUT = "timeout"
    SAFETY = "safety"
    UNKNOWN = "unknown"


class ProviderError(Exception):
    """Базовая ошибка провайдера с классификацией для failover-логики."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        raw_code: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retryable = retryable
        self.raw_code = raw_code
        self.retry_after = retry_after


class InvalidRequestError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        super().__init__(ErrorCategory.INVALID_REQUEST, message, **kw)  # type: ignore[arg-type]


class AuthError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        super().__init__(ErrorCategory.AUTH, message, **kw)  # type: ignore[arg-type]


class ForbiddenError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        super().__init__(ErrorCategory.FORBIDDEN, message, **kw)  # type: ignore[arg-type]


class RateLimitError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        kw.setdefault("retryable", True)
        super().__init__(ErrorCategory.RATE_LIMIT, message, **kw)  # type: ignore[arg-type]


class ServerError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        kw.setdefault("retryable", True)
        super().__init__(ErrorCategory.SERVER, message, **kw)  # type: ignore[arg-type]


class NetworkError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        kw.setdefault("retryable", True)
        super().__init__(ErrorCategory.NETWORK, message, **kw)  # type: ignore[arg-type]


class TimeoutError_(ProviderError):
    """Таймаут провайдера (не путать с builtin TimeoutError)."""

    def __init__(self, message: str, **kw: object) -> None:
        kw.setdefault("retryable", True)
        super().__init__(ErrorCategory.TIMEOUT, message, **kw)  # type: ignore[arg-type]


class SafetyError(ProviderError):
    def __init__(self, message: str, **kw: object) -> None:
        super().__init__(ErrorCategory.SAFETY, message, **kw)  # type: ignore[arg-type]


def classify_http_status(
    status: int, message: str, *, raw_code: str | None = None
) -> ProviderError:
    """Generic маппинг HTTP-статуса на ProviderError (провайдер может уточнить)."""
    if status == 400:
        return InvalidRequestError(message, status_code=status, raw_code=raw_code)
    if status == 401:
        return AuthError(message, status_code=status, raw_code=raw_code)
    if status == 403:
        return ForbiddenError(message, status_code=status, raw_code=raw_code)
    if status == 404:
        return InvalidRequestError(message, status_code=status, raw_code=raw_code)
    if status == 429:
        return RateLimitError(message, status_code=status, raw_code=raw_code)
    if status >= 500:
        return ServerError(message, status_code=status, raw_code=raw_code)
    return ProviderError(ErrorCategory.UNKNOWN, message, status_code=status, raw_code=raw_code)
