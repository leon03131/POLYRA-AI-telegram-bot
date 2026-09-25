# polyra-qa-release — wave3: A38 README актуализация + KNOWN_ISSUES правки + A36 CI

Дата: 2026-09-25. Исполнитель: polyra-qa-release (subagent). База: HEAD `e4ac383`
+ незакоммиченные изменения других агентов (shared worktree — app/**, tests/**
модифицированы не мной и мной не трогались).

## Scope (выдан lead)

Только: `README.md`, `KNOWN_ISSUES.md`, `.github/workflows/ci.yml` (новый),
этот отчёт. Не трогал: `app/**`, `miniapp/src/**`, `FIX_REPORT_V2.md`,
`TASKS.md`, `docs/vendor/**`, `docs/API.md`, `pyproject.toml`, `tests/**`.

## Файлы

| Файл | Изменение |
|---|---|
| `README.md` | правки (модели, env-таблица, тесты, структура, ограничения) |
| `KNOWN_ISSUES.md` | правки п.1/2/4/8/9, удалён дубль п.11; новых разделов нет |
| `.github/workflows/ci.yml` | НОВЫЙ — A36 |
| `.agents/reports/fix-v2/polyra-qa-release/wave3.md` | этот отчёт |

## A38 — README.md

1. **Модели**: добавлен **DeepSeek V4 Pro** (`deepseek-v4-pro`, text-only,
   thinking OFF/HIGH/MAX) рядом с DeepSeek V4.1 Flash — Flash НЕ заменён.
   Сверено с `app/llm/capabilities.py` (строки 128–139: `input_modalities=
   frozenset({"text"})`, `thinking_modes=(OFF, HIGH, MAX)`).
2. **Env-таблица** дополнена реально существующими в `app/config.py`
   переменными, которых не было: `SHOW_SOURCES` (default 1), парой
   `MAX_TOOL_CALLS_PER_ROUND` / `MAX_GENERATION_SECONDS` (4 / 240, A39);
   в compose-переменные добавлен `APP_BIND` (default `127.0.0.1`, A37 —
   сверено с docker-compose.yml:42).
3. **Тесты и качество кода**: команды приведены к верифицированным в
   FIX_REPORT_V2 (`pytest tests -q`, `ruff check .`, `mypy app tests scripts`;
   было `pytest` / `mypy .`). Убрано устаревшее «интеграционные прогоны
   планируются позже (RUN_GEMINI_INTEGRATION)» — `tests/integration/` уже
   существует (24 offline контрактных теста); live-проверка оформлена как
   ручной `smoke_providers.py --strict` (docker compose exec, читает ключи
   из БД — сверено с `scripts/smoke_providers.py:620-640`). Добавлена строка
   про CI.
4. **Структура проекта**: `scripts/` дополнен `smoke_providers.py`;
   `tests/unit/` → `tests/` (unit + integration).
5. **Известные ограничения** синхронизированы с KNOWN_ISSUES.md: убраны два
   протухших буллета («Docker-сборка не выполнялась / миграции при первом
   деплое», «probes не выполнялись» — оба закрыты 2026-09-24 на VPS);
   добавлены честные остатки: PostgreSQL barrier/race формально не прогнан,
   frontend E2E (Playwright) не поднимался, ссылка на деплой-заметки
   (IP-фильтрация хостера) в KNOWN_ISSUES.md.

## KNOWN_ISSUES.md (только правки, без новых разделов)

- **п.9 SSRF**: DNS rebinding / TOCTOU помечен закрытым 2026-09-24 (A19):
  pinned-connect `PinnedHTTPTransport` в `app/search/fetcher.py` (TCP/TLS к
  проверенному IP, Host/SNI по реальному хосту, TLS verify включён,
  per-redirect проверка). Оставлен как известная заметка с датой первичного
  риска (security review 2026-09-18, medium); опциональное усиление —
  egress-прокси.
- **п.1 миграции**: помечен закрытым 2026-09-24 — миграции 0001–0009 применены
  на проде (VPS SberCloud, живой PostgreSQL 16, healthcheck зелёный).
- **п.4 probes**: помечен закрытым (противоречил live-probe блоку того же
  файла) — 27/27 OK 18.09 + `--strict` 39 OK / 0 fail / 3 skipped 24.09.
- **п.8 Kimi max_output**: помечен закрытым 2026-09-24 (подтверждён владельцем,
  `max_output=1_048_576` в capabilities.py).
- **п.11**: удалён как точный дубль п.6 (Telegram draft rate limits).
- п.2 смягчён (runtime-путь подтверждён probe), остальные пункты не тронуты.

## A36 — .github/workflows/ci.yml (новый)

- Триггеры: `push`, `pull_request`. Без live API (весь pytest-набор offline:
  integration — in-memory фейки + `httpx.MockTransport`, сеть/БД/ключи не
  нужны — подтверждено заголовками tests/integration/*).
- Job `python` (ubuntu-latest): checkout → setup-python@v5 (3.12, cache pip по
  `pyproject.toml` — requirements-lockfile в проекте отсутствует) →
  `python -m pip install -e ".[dev]"` → `ruff check .` →
  `mypy app tests scripts` → `pytest tests -q`. Все команды — существующие,
  из pyproject/FIX_REPORT_V2.
- Job `frontend` (ubuntu-latest, working-directory `miniapp`): checkout →
  setup-node@v4 (22, cache npm по `miniapp/package-lock.json` — lockfile в
  репо есть) → `npm ci` → `npm run build` (= `tsc && vite build`, как в
  Dockerfile stage 1).
- YAML валидирован парсером (PyYAML safe_load: jobs python/frontend, шаги на
  местах).

## Что проверено

- Факты для правок сверены с кодом: `app/llm/capabilities.py` (модель Pro),
  `app/config.py` (env-дефолты), `app/search/fetcher.py` (pinned-connect),
  `docker-compose.yml` (APP_BIND/extra_hosts), `scripts/smoke_providers.py`
  (CLI `--provider all --strict`), `miniapp/package.json` (build-скрипт),
  наличие `miniapp/package-lock.json`, миграции `app/db/migrations/versions/
  0001..0009`.
- CI YAML парсится; команды соответствуют реальным скриптам/конфигам проекта.

## Не проверено (по инструкции lead)

- `npm ci` / `npm run build` / `tsc` не запускал — фронтенд у другого агента
  (frontend job в CI написан по package.json/Dockerfile, не прогнан локально).
- Python-гейты (ruff/mypy/pytest) не запускал — гейты у lead; мои файлы
  markdown/YAML на них не влияют.
- GitHub Actions прогон не выполнялся (нет удалённого пуша из этой среды) —
  первая валидация workflow произойдёт на первом push/PR.
- Докстринг `tests/unit/test_api.py:5-6` про несуществующий
  `RUN_API_INTEGRATION`-набор (находка final-review) — вне моего scope
  (tests/**), отмечаю для lead.
