"""Импорт Gemini API-ключей в пул проектов (таблица gemini_projects).

Запуск из корня репозитория:

    python scripts/import_gemini_keys.py --file keys.txt
    python scripts/import_gemini_keys.py --keys k1,k2 --name-prefix proj
    python scripts/import_gemini_keys.py --file keys.txt --dry-run

Ключи шифруются CryptoBox(master_encryption_key) и сохраняются как проекты
``<name-prefix>-01``, ``<name-prefix>-02``, ... с key_hint = последние 4 символа.
Импорт атомарен: конфликт имени (unique) откатывает всю партию.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.config import get_settings
from app.db.repositories import GeminiProjectRepository
from app.db.session import create_engine_from_url, make_session_factory
from app.security.crypto import CryptoBox, mask_secret

logger = logging.getLogger("import_gemini_keys")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбор CLI-аргументов."""
    parser = argparse.ArgumentParser(
        description="Импорт Gemini API-ключей в пул проектов (gemini_projects)"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--file",
        type=Path,
        help="Файл с ключами: один на строку; пустые строки и #-комментарии игнорируются",
    )
    source.add_argument("--keys", type=str, help="Ключи списком через запятую")
    parser.add_argument(
        "--name-prefix",
        default="proj",
        help="Префикс имён проектов (по умолчанию 'proj' → proj-01, proj-02, ...)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет импортировано (БД и ключи не трогаются)",
    )
    return parser.parse_args(argv)


def read_keys(args: argparse.Namespace) -> list[str]:
    """Список ключей из --file (строки, без пустых/#) или --keys (csv)."""
    if args.file is not None:
        # utf-8-sig: терпим BOM от Windows-редакторов.
        lines = args.file.read_text(encoding="utf-8-sig").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    return [key.strip() for key in args.keys.split(",") if key.strip()]


async def import_keys(keys: list[str], *, name_prefix: str, dry_run: bool) -> int:
    """Импортировать ключи; вернуть число (фактически или потенциально) добавленных."""
    names = [f"{name_prefix}-{index:02d}" for index in range(1, len(keys) + 1)]
    if dry_run:
        for name, key in zip(names, keys, strict=True):
            logger.info("[dry-run] проект %s ← %s", name, mask_secret(key))
        return len(keys)

    settings = get_settings()
    crypto = CryptoBox(settings.master_encryption_key)
    engine = create_engine_from_url(settings.database_url)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session:
            repo = GeminiProjectRepository(session)
            for name, key in zip(names, keys, strict=True):
                await repo.add(name, crypto.encrypt(key), key_hint=key[-4:])
                logger.info("добавлен проект %s (%s)", name, mask_secret(key))
            await session.commit()
    finally:
        await engine.dispose()
    return len(keys)


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    keys = read_keys(args)
    if not keys:
        logger.warning("ключей не найдено")
        return 1
    count = asyncio.run(import_keys(keys, name_prefix=args.name_prefix, dry_run=args.dry_run))
    logger.info("готово: %d ключей%s", count, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
