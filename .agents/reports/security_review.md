# Security Review — Telegram AI Assistant

- **Дата:** 2026-09-18
- **Ревьюер:** security-review subagent H (read-only, код не изменялся)
- **Scope:** `app/` (весь backend: api/auth, dependencies, middleware/access, services/access, все admin_* роуты, security/crypto, security/ssrf, search/fetcher + backends, observability/logging, llm/tools, services/generation, bot routers/streaming, config, db repositories), `miniapp/src` (XSS/хранение токена), `tests/` (наличие покрытия), `docs/API.md`, `PLAN.md`, `DECISIONS.md`, `.env.example`, `.gitignore`.
- **Метод:** статический read-only анализ + два точечных runtime-эксперимента над `app/observability/logging.py` (в .venv, без изменения файлов).

## Таблица находок

| Severity | Файл:строка | Проблема | Рекомендация |
|---|---|---|---|
| medium | app/observability/logging.py:30-39 | **Redaction не покрывает traceback'и.** `RedactionFilter` бежит до форматирования; `record.exc_text` в этот момент почти всегда `None` (заполняется в `Formatter.format` позже). Проверено вживую: `logger.exception(...)` с `ValueError('secret sk-…')` печатает секрет открытым текстом. Любое исключение, несущее ключ/токен в message (провайдеры, httpx, БД), попадёт в лог без маскировки. | Дополнительно прогонять `redact_text` по финальной строке в `_LineFormatter.format` (после `super().format()`), а не только фильтром по record. |
| medium | app/observability/logging.py:47-53 | **Redaction не покрывает `extra={}` поля.** `_LineFormatter` дописывает extra ПОСЛЕ фильтра. Проверено вживую: `logger.info('x', extra={'api_key': 'sk-…'})` → секрет в логе. Сейчас extra используется один раз с несекретным `current_chat_id` (services/chats.py:50), но механизм — дыра на будущее. | Применять `redact_text(str(value))` к extra-значениям в форматтере. |
| medium | app/services/generation.py (отсутствует), app/services/access.py:29-31 | **Per-user лимиты гранта не enforce'ятся.** `requests_per_day`, `token_limit`, `max_concurrent_generations` выставляются через admin API, отдаются в `/api/me`, но в generation path нигде не проверяются (grep по app/ — только set/display). Единственный реальный тормоз — одна активная генерация на чат (GenerationRegistry). Пользователь с грантом может гонять неограниченные генерации через N чатов → расход ключей/квот пула. | Либо реализовать проверку (счётчики generation_runs за сутки / активные генерации по user_id), либо явно зафиксировать в PLAN/DECISIONS, что лимиты — декларативные (M-future). |
| medium | app/security/ssrf.py:50-71 + app/search/fetcher.py:96-108 | **DNS rebinding / TOCTOU.** `assert_url_public` резолвит хост и валидирует IP, но httpx при connect резолвит DNS заново — pinning IP нет. Атакующий со своим DNS (short TTL / split-horizon) отдаёт публичный IP на проверку и приватный на коннект → SSRF в обход проверки (включая каждый redirect-hop). Классическое ограничение resolve-then-connect без pinning. | Задокументировать как принятый риск (личный бот, вызов только от модели с правом web_search). Усиление: кастомный httpx transport с connect на проверенный IP (Host/SNI по хосту) или egress-прокси с allowlist. |
| low | app/config.py:17 | **Захардкоженный дефолт owner:** `owner_telegram_id: int = 795063564` (личный TG id разработчика). Деплой без `OWNER_TELEGRAM_ID` в env молча делает владельцем этого человека. | Убрать дефолт (0/None) и добавить `OWNER_TELEGRAM_ID` в `validate_for_runtime()`. |
| low | app/config.py:22, .env.example:7 | **Дефолтный `gemini_base_url` — сторонний Netlify-proxy** (`extraordinary-piroshki-4e3b92.netlify.app`). Через него идут ВСЕ ключи пула Gemini (заголовок `x-goog-api-key`) и весь контент чатов. Если прокси не контролируется оператором — это передача секретов и данных третьей стороне «из коробки». | Осознанно задокументировать (в KNOWN_ISSUES/DECISIONS), либо дефолт = официальный Google endpoint, а прокси — opt-in. |
| low | app/api/auth.py:73-75 | **`auth_date` из будущего не отвергается:** проверяется только `age > max_age`. initData с будущим timestamp не устаревает никогда. Не эксплуатируется без bot token (подпись), но перехваченная initData и так replayable все 24ч — окно можно сузить. | Дополнительно отклонять `age < -300` (допуск на clock skew). |
| low | app/api/dependencies.py:54 + app/security/crypto.py:30 | **Key reuse + слабый KDF.** Один `master_encryption_key` — и источник Fernet-ключа, и HMAC-секрет session token. Конструкции разные (cross-protocol атаки нет), но компрометация ключа валит сразу оба механизма. Плюс fallback-деривация Fernet — голый SHA-256(passphrase): слабая passphrase брутфорсится офлайн по утёкшему шифротексту. | Отдельный `SESSION_SECRET` (или HKDF-деривация с разными info-строками). В .env.example уже рекомендуется `Fernet.generate_key()` — оставить как обязательное требование. |
| low | app/bot/routers/photos.py:39-48 | **Обход лимита фото при `file_size=None`:** предпроверка только если Telegram прислал размер; дальше `buf.read()` читает файл целиком в память (потолок — 20 МБ getFile Bot API). Также роутер использует свою константу 15 МБ, а не `settings.photo_max_bytes` — риск расхождения. | Проверять `len(data)` после скачивания; взять лимит из settings. |
| low | app/llm/tools/builtin.py:164, app/context/builder.py, config.py:27-30 | **Prompt-injection позиция слабее, чем заявляет ADR-012** («Web content — untrusted data (prompt-injection safe)»). Контент `open_url` уходит модели как `URL: …\n\n<текст>` без маркеров недоверенности; у web_search пометка только у AI Overview; дефолтный system prompt не инструктирует модель обращаться с web-контентом как с данными. Инъецированная страница может склонить модель к вызову remember/forget_memory/open_url. | Оборачивать tool-контент в явные delimiters («<<<UNTRUSTED WEB CONTENT>>>») + пункт в system prompt. |
| low | app/api/routes/auth.py:51-53, app/bot/middleware/access.py:60-62, app/bot/routers/commands.py:157 | **Owner-флаг в БД только выставляется, не снимается.** Смена `OWNER_TELEGRAM_ID` не отзывает `is_owner=True` у прежнего id; `/admin` в боте проверяет только `user.is_owner`. Отзыв владельца — только ручным SQL. | Задокументировать; опционально — при старте синхронизировать флаг с settings. |
| info | app/bot/middleware/access.py:46-48 | События без `from_user` (channel post, anonymous admin) проходят middleware в хендлеры, где падают на отсутствующем `user` (ловится error-middleware). Deny-by-default держится на косвенном механизме. | Явно дропать события без identity. |
| info | app/bot/dispatcher.py:59-60, app/bot/routers/stop.py | `stopped_message_generation` не покрыт AccessMiddleware (middleware навешан только на message/callback_query). Риск ничтожен: update приходит по аутентифицированному getUpdates, эффект — отмена генерации в том же чате, draft_id — 63-битный случайный. | Для порядка можно навесить middleware и на этот тип. |
| info | app/api/routes/auth.py (endpoint), app/api/app.py | Нет rate limiting на `POST /api/auth/telegram` и API в целом. Смягчается: невалидная initData отсекается до БД; HMAC-токен не брутфорсится; API по умолчанию на 127.0.0.1 за reverse proxy. | Rate limit на уровне nginx/proxy. |
| info | app/api/routes/admin_gemini.py:61, app/llm/gemini/pool.py:159 | `last_error_message` (тело ошибки провайдера/прокси, до 256 символов) пишется в БД и показывается в admin UI. Теоретически кастомный прокси может отразить фрагменты запроса. Регулярки redaction здесь не работают (не лог). | Прогонять `redact_text` перед персистом error_message. |
| info | DECISIONS.md:41 (ADR-008) | Неточность документации: «Fernet (AES-GCM)» — на деле AES-128-CBC + HMAC (authenticated). На безопасность не влияет. | Поправить формулировку. |

## Подтверждённые сильные стороны (проверено — ОК)

**Auth Mini App**
- `validate_init_data` — канонический алгоритм Telegram: `secret_key = HMAC("WebAppData", bot_token)`, data_check_string из отсортированных пар без `hash`, `hmac.compare_digest` (timing-safe), свежесть `auth_date` ≤ 24ч, `user` парсится как JSON, строгий `parse_qsl`. Покрыто unit-тестами (`tests/unit/test_api_auth.py`), включая tamper-случаи.
- Session token: `base64url(json{sub,exp}).hex(HMAC-SHA256)`, подпись по ASCII payload, `compare_digest`, `exp` enforced, типы `sub`/`exp` проверяются, TTL 900 с с автоматическим re-auth на фронте (miniapp/src/api/client.ts).
- Токен валидируется ДО обращения к БД (401/403 без PostgreSQL — dependencies.py:docstring и код).
- Owner не подделать: identity — только numeric `telegram_user_id` из подписанной initData / от Telegram Bot API; `is_owner` выставляется только при совпадении с `settings.owner_telegram_id`.
- Токен в Mini App хранится в памяти + sessionStorage (не localStorage), same-origin fetch, один retry на 401.

**Access control**
- КАЖДЫЙ admin-роутер проверен глазами (admin_access, admin_gemini, admin_providers, admin_search, admin_stats, admin_system, admin_users): все имеют `dependencies=[Depends(require_owner)]` на уровне роутера И `OwnerDep` в каждом хендлере. Пропущенных роутов нет.
- Deny-by-default: `evaluate_access` — banned/no_grant/suspended/revoked/expired; owner-байпас только по настроенному id; пользователь перечитывается из БД на каждый запрос (бан действует немедленно).
- Горизонтальная изоляция: чаты (`_get_own_chat` → 404 для чужих, без раскрытия существования), память (`user_id` в update/delete), callback «открыть чат» — префикс + UUID + проверка владельца.
- Deny-сообщения в боте generic («⛔ Доступ не активирован…»), без причин и внутренностей; error-middleware отвечает общим текстом.

**Secrets**
- Fernet (authenticated encryption) для Gemini/provider/search ключей; дешифровка только внутри pool/factory; в API отдаётся лишь `key_hint` (последние 4 символа) — проверены сериализаторы admin_gemini/admin_providers/admin_search, plaintext-ключи не возвращает ни один endpoint.
- В audit_log metadata — только key_hint/названия, секретов нет (services/admin.py:4).
- `.env` в `.gitignore`; `.env.example` — плейсхолдеры; поиск по `sk-…`, `AIza…`, telegram-token паттернам — только тестовые фикстуры в tests/.
- Старт падает без BOT_TOKEN/MASTER_ENCRYPTION_KEY (`validate_for_runtime`).
- Ключи провайдеров передаются в заголовках (Serper `X-API-KEY`, Brave `X-Subscription-Token`, Jina `Authorization: Bearer`, Gemini `x-goog-api-key`, Alibaba `Bearer`); сетевые ошибки сводятся к имени класса исключения, без URL/тел.
- Redaction-регулярки покрывают telegram-токены, `sk-*`, `Bearer …` и generic long tokens ≥33 символов (ловит Gemini/SerpApi-ключи, в т.ч. в URL query) — для обычных сообщений лога (дыры: traceback/extra — см. находки).

**SSRF**
- Схемы строго http/https; IP-литералы проверяются напрямую; блок всего неглобального (`is_global` + явный 169.254.169.254): loopback/private/link-local/reserved/multicast/unspecified; DNS-ошибки → SSRFError; проверка повторяется ПОСЛЕ КАЖДОГО redirect (≤5 хопов); content-type до чтения тела; stream-read с cap 2 МБ; connect/read timeout; `trust_env=False` (прокси из env не подхватывается). Покрыто `tests/unit/test_ssrf.py`. Остаточный риск — DNS rebinding (см. находки).

**Injection**
- SQL только через SQLAlchemy ORM/bound params; FTS — `plainto_tsquery` с биндом; f-string SQL не найден; `MemoryRepository.update_fields(**fields)` ограничен pydantic-схемами на call-site (mass-assignment недостижим извне).
- Tool args: JSON Schema (строгий поднабор, `additionalProperties: false`), permission-gate по `required_permission`, per-tool timeout, cap результата, аудит с обрезкой аргументов.
- Mini App: React, `dangerouslySetInnerHTML`/`innerHTML`/`eval` не найдены — XSS по коду чист.
- LLM-провайдеры не исполняют контент; tool loop ограничен `max_tool_iterations=8`.

**DoS**
- Фото ≤ 15 МБ (предпроверка; оговорка про `file_size=None` — в находках), fetch ≤ 2 МБ, tool timeouts (10-40 с), provider timeouts (search 10 с / long 30 с, gemini read 300 с), лимиты списков (chats 50, memory 100, audit ≤ 500, users ≤ 200, offset ≥ 0), одна активная генерация на чат, tool result caps. Не enforce'ятся per-user квоты гранта — см. находку.

**Telegram**
- Callback data: строгий префикс `chat:open:` + UUID parse + ownership.
- `draft_id` = 63-битный случайный (`uuid4 >> 65`, != 0) — коллизии/перебор непрактичны; stop сопоставлен по (chat_id, draft_id).
- Ошибки пользователю — по-русски, без stack trace/внутренностей (`user_error_message`).

**Конфиг**
- OpenAPI/docs/redoc выключены; CORS отсутствует осознанно (same-origin Mini App, задокументировано в app.py docstring); `api_host` default 127.0.0.1; глобальный 500-handler без деталей; `log_level` INFO по умолчанию.

## НЕ проверено / требует runtime

- Живой Telegram: interop валидации initData с реальными клиентами (тесты используют синтетические подписи), поведение `stopped_message_generation` от реальных клиентов, rate limits Bot API на draft updates (KNOWN_ISSUES #6).
- Реальные ключи: Fernet roundtrip с продовым `MASTER_ENCRYPTION_KEY`, отсутствие процедуры ротации master key (перешифровка существующих секретов не рассматривалась — судя по коду, не реализована).
- Поведение кастомного Gemini-прокси (`gemini_base_url` по умолчанию) — доверие к инфраструктуре владельца вне scope кода.
- Логгеры third-party (uvicorn/httpx/asyncpg): при `log_config` по умолчанию uvicorn вешает собственные handler'ы — обход RedactionFilter возможен вне root-logger (access_log=False выключен, остальное не проверялось вживую).
- Reverse proxy (TLS-терминация, rate limit) — вне репозитория.
- PostgreSQL: изоляция только на уровне приложения (нет RLS); SQL-дампы/бэкапы, права DB-пользователя — вне scope.
- Зависимости: аудит уязвимостей пакетов (pip-audit/npm audit) не выполнялся.

## Итог

Критичных и высоких находок **нет**. 4 medium (2 обхода redaction в логировании — подтверждены живым запуском; неработающие per-user лимиты грантов; DNS-rebinding TOCTOU в SSRF) и 6 low. Архитектурно контуры authN/authZ, шифрование секретов, SSRF-пайплайн и tool engine реализованы аккуратно и покрыты тестами.
