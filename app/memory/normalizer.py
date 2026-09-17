"""Нормализация текста памяти для сравнения при дедупликации."""

import re
import string

_PUNCTUATION = string.punctuation + "«»„“”‚‘’—–…№"
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_memory_text(text: str) -> str:
    """lower + strip + схлопнуть whitespace + убрать пунктуацию по краям слов.

    Простая нормализация для сравнения (дедупликация); исходный text хранится
    отдельно и не изменяется.
    """
    collapsed = _WHITESPACE_RE.sub(" ", text.lower().strip())
    words = [word.strip(_PUNCTUATION) for word in collapsed.split(" ")]
    return " ".join(word for word in words if word)
