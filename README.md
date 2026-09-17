# Telegram AI Assistant

Персональный AI-ассистент в Telegram: диалог ведётся в обычном чате с ботом
(текст, фото, стриминг ответа), а вся настройка и администрирование — через
Telegram Mini App (React + TypeScript + Vite). Backend: Python 3.12, aiogram 3,
FastAPI, PostgreSQL 16.

## Возможности

- **Модели**: Gemini 3.8 / 3.7 / 3.6 Flash (через пул проектов Google AI Studio
  с ротацией, квотами RPM/TPM/RPD и failover), Qwen 3.8 Flash / Max,
  DeepSeek V4.1 Flash, GLM 5.3, Kimi K3 (Alibaba Cloud Model Studio,
  OpenAI-compatible API).
- **Стриминг ответов** прямо в сообщение Telegram (draft-обновления) с кнопкой
  **Stop** для отмены генерации.
- **Фото**: сообщения с изображениями (vision-модели).
- **Web search / open_url** как function-calling tools (бэкенды поиска
  настраиваются в админке), плюс tools `set_chat_title`, `remember`,
  `forget_memory`.
- **Память**: автоматическое извлечение долговременных фактов, дедупликация,
  поиск через PostgreSQL FTS; просмотр/редактирование в Mini App.
- **Мульти-чат**: несколько диалогов, авто-заголовки, компакция контекста
  (summary вместо простого обрезания истории).
- **Админка** (только владелец): пользователи и доступ (grant/suspend/ban,
  лимиты, разрешённые модели), пул Gemini-проектов, ключ Alibaba, бэкенды
  поиска, системные настройки, статистика, аудит.

## Архитектура

```
Пользователи Telegram
        |
        v
 Telegram Bot API <=== long polling ===+
        ^                              |
        | сообщения / драфты           |
+-------+------------------------------+----------------+
| app (один процесс, контейнер :8080)                   |
|                                                       |
|  aiogram dispatcher -> Access middleware (user_id)    |
|    -> GenerationService                               |
|       -> ContextBuilder (+ компакция, авто-заголовки) |
|       -> ModelRegistry / Router                       |
|          -> GeminiProvider (пул проектов, квоты)      |
|          -> AlibabaProvider (OpenAI-compatible)       |
|       -> Tool Engine (web_search, open_url, ...)      |
|                                                       |
|  FastAPI (uvicorn): /api/* REST + статика Mini App    |
+-------+-----------------------------------------------+
        | SQLAlchemy 2 async (asyncpg)
        v
  PostgreSQL 16  (чаты, сообщения, зашифрованные ключи,
                  квоты, память, аудит)

Mini App (Telegram WebApp) --HTTPS--> [Caddy] --> app:8080
```

Правила изоляции: Telegram-хендлеры не знают про HTTP провайдеров и SQL;
провайдеры не знают про Telegram; всё через сервисный слой.

## Требования

- **Docker** и **Docker Compose** (v2).
- **Telegram Bot Token** — от [@BotFather](https://t.me/BotFather).
- **Gemini API-ключи** — один или несколько проектов Google AI Studio
  (импортируются в пул после запуска, см. ниже).
- **Alibaba API-ключ** — Alibaba Cloud Model Studio (для Qwen/DeepSeek/GLM/Kimi).
- **Домен с HTTPS** для Mini App (Telegram открывает Web App только по HTTPS).
  Простейший путь — встроенный профиль Caddy (автоматические сертификаты).

## Быстрый старт (production)

1. **Клонировать и настроить окружение:**

   ```bash
   git clone <repo-url> && cd aibot
   cp .env.example .env
   ```

   Заполнить в `.env`:
   - `BOT_TOKEN` — токен от BotFather;
   - `MASTER_ENCRYPTION_KEY` — сгенерировать:
     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
   - `OWNER_TELEGRAM_ID` — ваш числовой Telegram user id;
   - `APP_BASE_URL=https://your-domain.example` — публичный HTTPS-URL Mini App;
   - при желании `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`
     (по умолчанию `aibot` / `aibot` / `aibot`); пароль — только URL-safe
     символы (он подставляется в `DATABASE_URL`);
   - для Caddy-профиля: `DOMAIN=your-domain.example`.

   `GEMINI_BASE_URL` (proxy владельца) и `ALIBABA_BASE_URL` уже заданы —
   не менять.

2. **Собрать и запустить:**

   ```bash
   docker compose up -d --build
   docker compose logs -f app
   ```

   При старте контейнер сам применяет миграции (`alembic upgrade head`),
   затем запускает бота (long polling) и API. Проверка: приложение отвечает
   на `http://localhost:8080/health`.

3. **Импортировать Gemini-ключи** (шифруются `MASTER_ENCRYPTION_KEY`,
   атомарная партия, имена `proj-01`, `proj-02`, ...):

   ```bash
   docker compose exec -T app python scripts/import_gemini_keys.py --keys "key1,key2,key3"
   ```

   Или из файла (по одному ключу на строку, `#` — комментарии):

   ```bash
   docker cp keys.txt $(docker compose ps -q app):/tmp/keys.txt
   docker compose exec app python scripts/import_gemini_keys.py --file /tmp/keys.txt
   ```

   Полезные флаги: `--name-prefix myproj`, `--dry-run`.

4. **Alibaba-ключ**: Mini App → Admin → Providers (ключ хранится
   зашифрованным в БД) — либо bootstrap через `ALIBABA_API_KEY` в `.env`.
   Там же (Admin → Search) добавляются ключи бэкендов поиска для web_search.

5. **HTTPS / reverse proxy** (опционально, если нет своего nginx):

   ```bash
   docker compose --profile caddy up -d
   ```

   Caddy сам получит сертификат Let's Encrypt для `DOMAIN` (A-запись домена
   должна указывать на сервер; порты 80/443 открыты).

6. **Привязать Mini App в Telegram:**
   - при первом `/start` бот сам ставит кнопку меню «Настройки» на
     `APP_BASE_URL` для вашего чата;
   - глобально для всех: [@BotFather](https://t.me/BotFather) → `/setmenubutton`
     → выбрать бота → указать URL `https://your-domain.example`;
   - админка: команда `/admin` у бота (только для владельца) открывает
     `{APP_BASE_URL}/admin`.

**Обновление:** `git pull && docker compose up -d --build` — миграции БД
применятся автоматически при старте контейнера.

## Локальная разработка

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

# Нужна локальная PostgreSQL 16 (или из compose):
#   docker compose up -d postgres
#   предварительно раскомментировав проброс порта 127.0.0.1:5432 в docker-compose.yml
alembic upgrade head

python -m app.main            # бот (long polling) + API на 127.0.0.1:8080

cd miniapp
npm install
npm run dev                   # vite dev server; /api проксируется на 127.0.0.1:8080
```

## Конфигурация

Все переменные читаются из окружения / `.env` (pydantic-settings). В Docker
`DATABASE_URL` и `API_HOST` переопределяются в `docker-compose.yml`.

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — (обязательна) | Токен бота от BotFather |
| `OWNER_TELEGRAM_ID` | `795063564` | Числовой user id владельца (полный доступ) |
| `MASTER_ENCRYPTION_KEY` | — (обязательна) | Fernet-ключ для шифрования API-ключей в БД |
| `DATABASE_URL` | `postgresql+asyncpg://aibot:aibot@localhost:5432/aibot` | DSN PostgreSQL |
| `APP_BASE_URL` | `https://localhost` | Публичный HTTPS-URL Mini App (кнопки бота) |
| `TELEGRAM_PROXY` | — | Прокси для Bot API, напр. `socks5://127.0.0.1:1080` |
| `GEMINI_BASE_URL` | proxy владельца (в `.env.example`) | Endpoint Gemini API |
| `ALIBABA_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Endpoint Alibaba (не менять) |
| `ALIBABA_API_KEY` | — | Bootstrap-ключ Alibaba (основной путь — через админку) |
| `DEFAULT_MODEL` | `gemini-3.8-flash` | Модель по умолчанию |
| `DEFAULT_SYSTEM_PROMPT` | см. `app/config.py` | Системный промпт по умолчанию |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8080` | Адрес API (в образе `API_HOST=0.0.0.0`) |
| `MINIAPP_DIST` | `miniapp/dist` | Каталог собранного Mini App |
| `SESSION_TOKEN_TTL_SECONDS` | `900` | TTL сессионного токена Mini App |
| `PHOTO_MAX_BYTES` | `15728640` | Лимит размера фото |
| `MAX_TOOL_ITERATIONS` | `8` | Лимит раундов tool-calling loop |
| `RECENT_HISTORY_LIMIT` | `20` | Сообщений в «сырой» истории |
| `CONTEXT_KEEP_RECENT` / `CONTEXT_TRIGGER_RATIO` / `COMPACTION_MIN_SEGMENT` | `10` / `0.7` / `6` | Параметры компакции контекста |
| `SUMMARY_MODEL` / `SUMMARY_THINKING` | `gemini-3.5-flash-lite` / `medium` | Модель summary |
| `TITLE_MODEL` / `TITLE_THINKING` | `gemini-3.5-flash-lite` / `low` | Модель авто-заголовков |
| `MEMORY_MODEL` / `MEMORY_THINKING` | `gemini-3.5-flash-lite` / `medium` | Модель извлечения памяти |
| `MEMORY_RETRIEVAL_LIMIT` / `MEMORY_EXTRACTION_MIN_CHARS` / `MEMORY_DEDUP_THRESHOLD` | `5` / `200` / `0.85` | Параметры памяти |

Переменные compose (не уходят в контейнер приложения, только в подстановку):
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (по умолчанию `aibot`),
`APP_PORT` (по умолчанию `8080` — наружный порт), `DOMAIN` (для профиля caddy).

## Тесты и качество кода

```bash
pytest                # unit-тесты (tests/unit), asyncio_mode=auto
ruff check .          # линтер
mypy .                # строгая типизация (app — strict; tests/alembic — послабления в pyproject)
```

Все тесты unit-уровня: сеть и реальные API-ключи не требуются. Интеграционные
прогоны против живых провайдеров планируются позже под отдельными флагами
окружения (вида `RUN_GEMINI_INTEGRATION`) — по умолчанию выключены.

## Безопасность

- Доступ к боту — только по числовому `telegram_user_id` (access middleware);
  полные права — у `OWNER_TELEGRAM_ID`, остальные — через выдачу доступа
  в админке.
- Mini App API: валидация `initData` по HMAC-подписи Telegram + `auth_date`
  (≤ 24 ч); далее — короткоживущий session token (HMAC на master-ключе).
- API-ключи провайдеров и бэкендов поиска хранятся в БД зашифрованными
  (Fernet, `MASTER_ENCRYPTION_KEY`); наружу отдаётся только `key_hint`
  (последние 4 символа).
- `open_url` — SSRF-safe: блокировка приватных/служебных диапазонов адресов,
  контроль редиректов.
- PostgreSQL не пробрасывается наружу из docker-сети; приложение в контейнере
  работает под non-root пользователем.

## Структура проекта

```
app/
  main.py            точка входа: bot polling + uvicorn (один процесс)
  config.py          pydantic-settings (env / .env)
  bot/               aiogram: роутеры, middleware, streaming-драфты
  api/               FastAPI: /api роуты, auth, статика Mini App
  services/          generation, chats, access, admin, llm_factory
  llm/               провайдеры (gemini, alibaba), registry, tool engine,
                     gemini/ — пул проектов, квоты
  context/           context builder, компактор, заголовки
  memory/            извлечение/дедуп/поиск памяти (Postgres FTS)
  search/            бэкенды web_search + fetcher (SSRF-safe)
  security/          crypto (Fernet), ssrf
  db/                модели, репозитории, migrations (alembic)
  observability/     логирование
miniapp/             React + TS + Vite (Telegram Mini App)
scripts/             import_gemini_keys.py — импорт ключей в пул Gemini
docs/                API.md (контракт Mini App API), vendor-заметки
tests/unit/          unit-тесты
Dockerfile, docker-compose.yml, Caddyfile, .dockerignore
```

## Известные ограничения

- **Reasoning (thinking) никогда не показывается пользователю** — by design.
- **Драфты стриминга работают только в приватных чатах** (ограничение
  `sendMessageDraft` в Bot API).
- **Rate limits драфтов подобраны эмпирически** (~1 обновление/сек +
  обработка 429 `retry_after`); официальной документации лимитов нет.
- Docker-сборка в dev-среде не выполнялась (Docker недоступен) — миграции
  против живой PostgreSQL проверяются при первом деплое.
- Часть фактов по Gemini восстановлена по архивам документации (ai.google.dev
  был недоступен в dev-сети); runtime-проверки провайдеров (capability probes)
  не выполнялись без реальных ключей.
