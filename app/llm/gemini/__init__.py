"""Пул Gemini-проектов: квоты (quota) и ротация ключей с failover (pool).

ADR-005 (правила failover), ADR-015 (ключ через request.metadata).
"""

from app.llm.gemini.pool import (
    GeminiProjectPool,
    PooledCredential,
    PoolExhaustedError,
    ProjectInfo,
    ProjectStore,
)
from app.llm.gemini.quota import (
    PACIFIC,
    QuotaLimits,
    QuotaStore,
    QuotaTracker,
    UsageSnapshot,
    current_minute,
    pacific_day,
)

__all__ = [
    "PACIFIC",
    "GeminiProjectPool",
    "PooledCredential",
    "PoolExhaustedError",
    "ProjectInfo",
    "ProjectStore",
    "QuotaLimits",
    "QuotaStore",
    "QuotaTracker",
    "UsageSnapshot",
    "current_minute",
    "pacific_day",
]
