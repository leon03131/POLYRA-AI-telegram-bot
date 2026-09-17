"""Дедупликация кандидатов памяти: exact match + Jaccard по токенам."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models import Memory


def jaccard_tokens(a: str, b: str) -> float:
    """Jaccard-сходство множеств токенов (split по пробелам). Пустые → 0.0."""
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def find_duplicate(
    new_text_norm: str, existing: list[Memory], *, threshold: float
) -> Memory | None:
    """Найти дубликат среди existing: сначала exact normalized match,
    затем запись с max jaccard >= threshold; None — дубликата нет."""
    if not new_text_norm:
        return None
    best: Memory | None = None
    best_score = 0.0
    for memory in existing:
        if memory.normalized_text == new_text_norm:
            return memory
        score = jaccard_tokens(new_text_norm, memory.normalized_text)
        if score > best_score:
            best, best_score = memory, score
    if best is not None and best_score >= threshold:
        return best
    return None
