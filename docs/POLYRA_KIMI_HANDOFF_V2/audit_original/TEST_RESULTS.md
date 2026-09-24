# Реальные результаты проверок

## Что выполнено

Дата 24.09.2026; Python 3.13.5, Node 22.16.0, глобальный TypeScript 5.8.3. Это среда аудита, не результат чистого pip/npm install проекта. Положительный syntax result не равен import/typecheck/runtime success.

- 145 Python-файлов: AST parse + compile; ошибок нет.
- 36 TS/TSX-файлов: syntax parser; ошибок нет. Это НЕ `npm run typecheck` и НЕ `npm run build`.
- 5 JSON + TOML разобраны.
- Полный pytest collection: 285 cases собраны и 4 ошибки сбора из-за отсутствующего aiogram. В снимке 18 test modules и 290 определений test-функций; definitions и parametrized cases — разные числа, делить одно на другое как процент покрытия нельзя.
- Доступный subset без четырёх модулей: 285 passed in 3.97s.
- `alembic upgrade head --sql`: успешная offline генерация всех 8 миграций под PostgreSQL. Не выполнялась ни одна реальная транзакция PostgreSQL.
- 9 offline диагностик в reproduce_findings.py; 3 в additional_checks.py; отдельная JS-проверка UUID → NaN. В SQLite включён foreign_keys=ON, используются оригинальные declarative models; это доказательство конкретной CASCADE-семантики, не PostgreSQL integration suite.
- Повторная SHA-256 проверка всех 215 исходных файлов: неизменны.
- Эвристический поиск bot-token/Google-key/sk/private-PEM patterns: 0 кандидатов. Не проверялась вся история Git и не доказывается отсутствие секретов любых форматов.

## Почему не выполнена вся приёмка

В среде отсутствуют aiogram, asyncpg, необходимые SDK/инструменты Ruff/mypy/respx; установка зависимостей не удалась из-за сетевого DNS. `npm ci --offline` остановился на отсутствующей cached зависимости yallist@3.1.1. Поэтому не запускались настоящий frontend build/typecheck/E2E, полный app imports/runtime, Docker Compose и миграции на PostgreSQL. Владелец не предоставлял live-секреты для их использования в аудите; real provider/Telegram requests не делались.

Это ограничения среды аудита, а не доказательство, что установка проекта у владельца всегда падает. Одновременно эти ограничения не отменяют ошибок, непосредственно воспроизведённых доступными production functions и контрактами.

## Логи

| Файл | Что подтверждает |
|---|---|
| evidence/pytest_collection.log | Четыре ошибки сбора и доступные cases |
| evidence/pytest_available.log | Реальный результат 285 passed |
| evidence/install.log | Почему не удалось загрузить Python dependencies |
| evidence/npm_install.log, npm_alternate_cache.log | Неуспешные offline install attempts |
| evidence/alembic_offline.log, alembic_upgrade.sql | Успешное построение SQL, не его выполнение |
| evidence/reproduced.json | Девять воспроизведений defects |
| evidence/additional_checks.json | /admin 404, disabled credential fallback, chat-deletion ledger |
| evidence/typescript_syntax.json | Syntax checks и UUID Number(id) пример |
| evidence/static_analysis.json | Все файлы, syntax, AST imports/definitions/assertions, docs claims |
| evidence/inventory.json | Исходные размеры/строки/SHA-256 |
| evidence/secret_pattern_scan.json | Ограниченный pattern scan |

## Как повторить доступные unit tests

Из корня проекта, с установленными доступными зависимостями:

```sh
python -m pytest -q tests/unit \
  --ignore=tests/unit/test_bot_helpers.py \
  --ignore=tests/unit/test_bot_wiring.py \
  --ignore=tests/unit/test_draft_streamer.py \
  --ignore=tests/unit/test_generation.py
alembic upgrade head --sql
```

Исключение этих файлов здесь описывает ограничения аудита. Это НЕ разрешение исключать их из финального CI. В полноценной среде разработчик должен выполнить `python -m pip install -e '.[dev]'`, полный pytest, Ruff, mypy, `npm ci`, typecheck/build, реальные PostgreSQL/Docker checks и отдельные opt-in integration tests.
