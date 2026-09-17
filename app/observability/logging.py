"""Stdlib logging setup: one-line stdout format with secret redaction."""

import contextlib
import logging
import re
import sys

from app.security.crypto import mask_secret

_TELEGRAM_TOKEN_RE = re.compile(r"(\d{6,}):[A-Za-z0-9_-]{20,}")
_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9]{8,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{33,}(?![A-Za-z0-9+/_-])")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"asctime", "message"}


def redact_text(text: str) -> str:
    """Mask Telegram tokens, API keys, Bearer tokens and long hex/base64 secrets."""
    text = _TELEGRAM_TOKEN_RE.sub(r"\1:***", text)
    text = _API_KEY_RE.sub("sk-***", text)
    text = _BEARER_RE.sub(r"\1 ***", text)
    return _LONG_TOKEN_RE.sub(lambda match: mask_secret(match.group(0)), text)


class RedactionFilter(logging.Filter):
    """Redact secrets from a log record by rewriting its rendered msg/args safely."""

    def filter(self, record: logging.LogRecord) -> bool:
        with contextlib.suppress(Exception):
            if record.args:
                record.msg = redact_text(record.getMessage())
                record.args = ()
            elif isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if record.exc_text:
                record.exc_text = redact_text(record.exc_text)
        return True


class _LineFormatter(logging.Formatter):
    """One-line formatter: extras как key=value + redaction ПОСЛЕДНЕЙ строки.

    Redact на уровне финальной строки покрывает и traceback (exc_info),
    и extra-поля — фильтр на record этого не гарантирует.
    """

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = [
            f"{key}={value!r}"
            for key, value in sorted(vars(record).items())
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_")
        ]
        if extras:
            line = f"{line} {' '.join(extras)}"
        return redact_text(line)


def setup_logging(level: str) -> None:
    """Configure the root logger: a single stdout StreamHandler with redaction."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_LineFormatter(_LOG_FORMAT))
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
