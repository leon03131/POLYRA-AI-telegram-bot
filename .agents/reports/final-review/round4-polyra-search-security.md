# Final review — round 4 — polyra-search-security (tools / search / SSRF / crypto)

**Автор:** final-review subagent (READ-ONLY баг-хантинг)
**Дата:** 2026-09-26
**Checkout:** O:\work\aibot, HEAD `4925c40` (503 теста зелёные, mypy/ruff чистые; git-операции и live-вызовы не выполнялись)
**Scope:** `app/search/**` (manager, serper, serpapi_ai_overview, brave, jina, playwright_google, reader, fetcher, base, `__init__`), `app/security/ssrf.py`, `app/security/crypto.py`, `app/llm/tools/**` (registry, runner, schemas, builtin; `memory.py`/`chat_title.py` в `app/llm/tools` НЕ существуют — их роли: `app/memory/*`, `app/context/titles.py`), связанные части `app/services/generation.py` (`_resolve_tool_defs`, `collect_sources`/`extract_sources`, `_resolve_jina_reader`, tool loop), `tests/unit/test_{search,ssrf,tools,crypto}.py` (+ `test_generation.py`), попутно `app/api/routes/admin_search.py`, `app/api/routes/chats.py`, `app/bot/routers/commands.py`, `app/bot/dispatcher.py`, `app/bot/streaming/draft.py`, `app/db/repositories/{search_configs,memories}.py`.
`app/search/security.py` не существует (в scope-описании упомянут ошибочно — так же, как в прошлом раунде).

## Метод / прогоны

- Полное чтение всех файлов scope (file:line, цитаты ниже).
- Офлайн-прогоны:
  - `.venv\Scripts\python -m pytest tests/unit/test_search.py tests/unit/test_ssrf.py tests/unit/test_tools.py tests/unit/test_crypto.py -q` → **132 passed, exit 0**.
  - `+ tests/unit/test_generation.py -q` → **164 passed, exit 0**.
  - `.venv\Scripts\python -m mypy app/search app/security app/llm/tools` → **Success: no issues in 18 source files**.
  - Симуляции (MockTransport / прямой вызов): A32-формат → `extract_sources`/`collect_sources`; truncation-граница 4000; `_pin_request` для IPv6-литералов/портов/userinfo/trailing dot; redirect-loop; `data:`-redirect; gzip-бомба 20 МБ при max_bytes=1 МБ. Скрипты: `C:\Users\LEON03~1\AppData\Local\Temp\opencode\round4_{search,pin,fetch}_sim.py`.
- Live-вызовы, git-операции — запрещены и не выполнялись.

---

## P1 — A32-переезд 0be0524: «Источники» теряют заголовки (title==url) во ВСЕХ ответах с web_search

**Файлы:** `app/llm/tools/builtin.py:141-150`, `app/services/generation.py:217-253,473-477`.

Коммит `0be0524` (A32) переставил SOURCES-строку в начало tool-результата:

```python
# builtin.py:141-144
# A32: SOURCES — ПЕРВОЙ частью: обрезка max_result_size режет хвост,
# citations обязаны пережить truncation.
if results:
    parts.append("SOURCES: " + " | ".join(result.url for result in results))
```

Консьюмер `extract_sources` идёт по строкам В ПОРЯДКЕ (generation.py:224-238): строка `SOURCES:` теперь встречается ПЕРВОЙ и добавляет пары `(url, url)` до нумерованных записей `(title, url)`. `collect_sources` дедуплицирует по url с сохранением ПЕРВОГО вхождения (generation.py:246-252) — до 0be0524 первыми были нумерованные записи с заголовками, и SOURCES был запасным источником для усечённого хвоста; теперь `(url,url)` побеждает для каждого URL, а записи с реальными `title` отбрасываются как дубликаты.

**Симуляция (реальный вывод `_format_search_outcome` → `collect_sources` → `build_sources_suffix`):**

```
SOURCES: https://docs.python.org/3/ | https://peps.python.org/pep-0008/

1. Python docs
https://docs.python.org/3/
The official docs
...
=== collect_sources -> footer ===
**Источники:**
1. [https://docs.python.org/3/](https://docs.python.org/3/)
2. [https://peps.python.org/pep-0008/](https://peps.python.org/pep-0008/)
```

Старый формат (до 0be0524) давал `1. [Python docs](https://docs.python.org/3/)` — подтверждено той же симуляцией («OLD format extract_sources»: сначала `('Python docs', …)`).

**Прод-проявление:** каждый ответ с web_search и `show_sources=True` (дефолт, config.py:37) показывает в Telegram «Источники» с URL в качестве и текста ссылки, и цели — заголовки страниц исчезли. Регрессия тотальна для фичи, не edge-case.

**Почему тесты зелёные:** `test_generation.py:380-415` и `test_tools.py:589-612` тестируют СТАРЫЙ порядок (numbered-список отдельно, `SOURCES:` на поздней строке; assert `"SOURCES: https://a | https://b" in content` — порядок не проверяет). Тестов, прогоняющих реальный вывод `_format_search_outcome` через `extract_sources`, нет — разрыв producer/consumer не покрыт.

**Рекомендация (владельцу формата):** либо отдавать пары (title,url) структурно (tool-records уже содержат `SearchResult`), либо в `extract_sources` обрабатывать нумерованные записи ДО маркер-строки, либо дедуплицировать с приоритетом записей с осмысленным title (title != url).

---

## P2-1 — A32 + max_result_size: обрезка посреди SOURCES-строки кладёт битый частичный URL в пользовательские «Источники»

**Файлы:** `app/llm/tools/runner.py:148-150`, `app/llm/tools/registry.py:33` (default 4000), `builtin.py:141-144`, `generation.py:226-230`.

`runner._finish` режет хвост: `content[:max_size] + "… [truncated]"`. Цель A32 достигнута (SOURCES переживает обрезку), но при `max_results=10` (schema max) и длинных URL (jina/serpapi отдают path-heavy URL) строка SOURCES сама длиннее 4000 — обрезка падает в середину URL.

**Симуляция (10 результатов × ~300-симв. URL, len(content)=8547):**

```
truncated len=4013; parsed 12 sources; last source =
  'https://example1.com/ppp… [truncated]'
```

`extract_sources` парсит частичную строку `split("|")` — последний элемент неполный и содержит маркер `… [truncated]`; он попадает в `collect_sources` → `build_sources_suffix` → финальное сообщение как «источник». При ещё более длинных URL «источником» становится вообще не-URL (`'pppp… [truncated]'`). Модель видит тот же мусор в tool-результате и может процитировать его.

**Прод-проявление:** редкий, но реалистичный случай (10 результатов, длинные URL) — в «Источниках» появляется битая ссылка-огрызок с `… [truncated]`; для rich-tier markdown с `[a](b)` незакрытые скобки могут валить парсинг (fallback на plain).

**Рекомендация:** резать по границе записи (после `|`), не добивать хвост маркером внутрь URL; или отфильтровывать в `extract_sources` элементы без `://`/с маркером truncation.

---

## P2-2 — SSRF/PinnedHTTPTransport: IPv6-литерал → некорректный Host-заголовок (RFC 7230), с портом — двусмысленный

**Файлы:** `app/search/fetcher.py:53-74` (`PinnedHTTPTransport._pin_request`), `app/security/ssrf.py:66-71` (hostname из `urlparse` — БЕЗ скобок).

`real_host` берётся из `validate_url_public_ips` → `urlparse(url).hostname`, который для `http://[2001:db8::1]:8080/x` возвращает `2001:db8::1` (без квадратных скобок). `_pin_request` перезаписывает Host этим значением, хотя httpx сам по умолчанию ставит корректный Host (скобки обязательны для IPv6-литерала в Host, RFC 7230 §5.4).

**Симуляция (httpx 0.28.1, прямой вызов `_pin_request`):**

```
orig url : http://[2001:db8::1]/path
  Host header        : '2001:db8::1'          # httpx сам поставил бы '[2001:db8::1]'
orig url : http://[2001:db8::1]:8080/path
  Host header        : '2001:db8::1:8080'     # httpx сам поставил бы '[2001:db8::1]:8080'
```

Для IP-литералов `real_host == pinned_ip`, перезапись Host бессмысленна и вредна: сервер (nginx и т.п.) отвечает 400 или уходит в дефолтный vhost; вариант с портом неотличим от мусора. Для https-литералов дополнительно SNI `2001:db8::1` невалиден по RFC 6066 (fail-closed на handshake). DNS-имя, пиннингуемое на IPv6 (`real_host` = имя), — корректно; ломается только литеральный случай.

**Прод-проявление:** `open_url("http://[IPv6]/…")` от модели → fetch падает/мисрутится (SearchBackendError «network error»), не SSRF-байпас (connect остаётся на проверенном IP). Тесты покрывают только IPv4-литерал и IPv4-pinned (`test_search.py:698-752`); IPv6-литерал в Host не покрыт.

**Рекомендация:** в `_pin_request` брать Host из `request.url.netloc`-нормализации httpx (или добавить скобки, если `real_host` содержит `:`), либо не перезаписывать Host, когда `real_host` == IP-литерал.

---

## P2-3 — chat title: HTML-инъекция в сообщение бота (default parse_mode=HTML) + поломка отправки

**Файлы:** `app/bot/dispatcher.py:38-42` (Bot default `parse_mode=ParseMode.HTML`), `app/bot/routers/commands.py:140`, `app/api/routes/chats.py:24-33,122-124` (title без санитизации, max_length=256), `app/context/titles.py:34-47` (`sanitize_title` не экранирует HTML).

Единственное место, где пользовательские данные интерполируются в сообщение БЕЗ `parse_mode=None`:

```python
# commands.py:140
await callback.message.answer(f"Открыт чат: {chat.title or 'Без названия'}")
```

Chat title попадает в БД двумя путями: (a) Mini App API `POST/PATCH /api/chats` — только `max_length=256`, БЕЗ `sanitize_title`/экранирования; (b) модельные пути (`set_chat_title` tool, `TitleGenerator`) — через `sanitize_title`, который срезает кавычки/переводы строк и ≤60 симв., но НЕ экранирует `<`, `>`, `&`. Кнопки `/chats` (commands.py:111-118) безопасны — текст InlineKeyboardButton не парсится parse_mode; но подтверждение «Открыт чат: …» парсится как HTML.

**Прод-проявление:**
- title `a<b>c d e` → TelegramBadRequest «can't parse entities» → `answer()` бросает исключение ПОСЛЕ `callback.answer("Чат выбран")` (чат уже переключён, но сообщение падает в error-хендлер aiogram);
- title с валидными тегами (`<a href="https://phish">…</a>`, `<b>…`) → рендерится как разметка/кликабельная ссылка в сообщении бота (self-XSS/спуфинг в собственном чате пользователя).

Весь остальной пользовательский контент отправляется с `parse_mode=None` (draft.py:179,201,208,225,284-318; generation.py:1213) — ровно потому, что default HTML (комментарий A20 в draft.py:21-23 сам это декларирует). commands.py:140 — пропущенный случай.

**Рекомендация:** `parse_mode=None` в commands.py:140 (и/или экранирование/`sanitize_title` в API-путях chats.py).

---

## P3-1 — httpx.InvalidURL из редиректа с невалидным Location уходит из классификации fetcher

**Файлы:** `app/search/fetcher.py:150-171` (`_single_hop` ловит только `httpx.TimeoutException`/`httpx.HTTPError`), httpx 0.28.1 `_send_handling_redirects` (проверено в venv: `_build_redirect_request` вызывается ВСЕГДА, даже при `follow_redirects=False`, для `response.next_request`).

30x с `Location: data:text/html,<script>…` (или иным невалидом) → httpx бросает `InvalidURL` («For absolute URLs, path must be empty or begin with '/'») ДО нашего ручного urljoin/validate. `InvalidURL` — НЕ подкласс `httpx.HTTPError` (MRO: InvalidURL→Exception), docstring fetcher'а «httpx-ошибки → SearchBackendError» нарушен; исключение пробрасывается вверх как есть. `_open_url_handler._is_ssrf_error` его не распознаёт (нет «ssrf/policy/blocked/unsafe/forbidden» в имени) → runner гасит в generic «Tool error» вместо «URL запрещён политикой безопасности».

**Симуляция:** `DATA REDIRECT: InvalidURL: For absolute URLs, path must be empty or begin with '/'` (трейсбек через `fetch_url` → `_single_hop` → httpx `_build_redirect_request`).

**Прод-проявление:** модель видит «Tool error» вместо понятного «URL запрещён»; fail-closed (запрос не отправляется), SSRF-байпаса НЕТ — просто неклассифицированная ошибка. Отмечу: валидный `data:`-Location (без спецсимволов) корректно блокируется нашим `validate_url_public_ips` (urljoin → scheme 'data' → SSRFError, проверено симуляцией).

**Рекомендация:** добавить `except httpx.InvalidURL` в `_single_hop` → `SearchBackendError(f"open_url: invalid redirect Location")`.

## P3-2 — Бюджеты длин не сходятся: reader 8000 / html_to_text 20000 / tool max_result_size 4000

**Файлы:** `app/search/jina.py:123` (`max_chars: int = 8000`), `app/search/reader.py:75` (`max_chars=20000`), `app/llm/tools/registry.py:33` (default `max_result_size=4000`), `builtin.py:277-286` (open_url без override). Всё, что читалка добыла сверх ~4000, гарантированно срезается в `runner._finish` — половина Jina-текста и 3/4 локальной экстракции не доходят до модели. Не баг безопасности — несогласованность лимитов (плюс маркеры недоверенного контента ~150 симв. съедают бюджет).

## P3-3 — Reader-таймаут 30 с при tool-бюджете open_url 30 с всего

**Файлы:** `app/search/jina.py:122` (`timeout=30.0`, X-Timeout: 30), `app/llm/tools/builtin.py:283` (`timeout=30.0`), `app/search/fetcher.py:178` (`timeout: float = 20.0`). Любой fetch > 0 с оставляет reader'у < 30 с его собственного X-Timeout — часто будет сниматься `asyncio.wait_for`-ом по tool-таймауту (статус timeout, модель теряет и reader-текст, и уже скачанный локальный fallback, т.к. отмена происходит ВНУТРИ `_extract_text` → wait_for отменяет handler целиком). Fallback на локальную экстракцию не успевает сработать.

## P3-4 — ToolContext.settings — env-снимок, а не effective (A13-непоследовательность)

**Файлы:** `app/services/generation.py:751-759` (`settings=self._settings`), против `generation.py:998-1009` (effective_settings в `_prepare`). `_remember_handler` (builtin.py:225) читает `context.settings.memory_dedup_threshold` из env-значения, тогда как extraction/builder уже на DB-effective настройках. Замечено и в прошлом раунде (A12 partial) — не исправлено в 0be0524/4925c40.

## P3-5 — manager._get_backend: aclose инстанса при смене конфига может убить in-flight запрос

**Файлы:** `app/search/manager.py:105-117`. Смена enabled/ключа в admin → `entry.instance.aclose()` под lifecycle-lock, но параллельный `backend.search()` (уже начатый) получит «client closed» от httpx → SearchBackendError → fallback. Однократный транзиент, не crash. Аналогично in-memory cooldown playwright (manager.py:50,280-297) — per-process (multi-worker → cooldown не шарится; рестарт сбрасывает) — документировано как experimental, принято.

## P3-6 (наблюдение) — прочее проверенное, деградации без дыр

- **Trailing dot** (`http://example.com./x`): SSRF резолвит и пиннит корректно; TLS-серт обычно не матчится с `example.com.` → fail-closed («network error»), не байпас.
- **Userinfo** (`http://127.0.0.1@evil.com/`): `urlparse.hostname` = `evil.com` — верная сторона `@` проверяется; userinfo сохраняется в URL → httpx шлёт basic-auth ТОЛЬКО этому же хосту; urljoin при cross-host redirect сбрасывает userinfo.
- **304/3xx-не-redirect** без Location: проходит как «нормальный» ответ с пустым телом — безвредно.
- `_fetch_raw_single` пиннит только `ips[0]` (sorted) — при multi-A/AAAA нет ретрая на другие IP — функциональное ограничение, не безопасность.

---

## OK — проверено и чисто (по чек-листу охоты)

1. **P0-класс (runtime имена/сигнатуры): не найдено.** mypy по scope — 0 ошибок. Сверено вручную: `manager.get_reader` (manager.py:139) ↔ `generation.py:495` ✓; `manager.health_check` (manager.py:301) ↔ `admin_search.py:121` ✓; `_BACKEND_FACTORIES` (manager.py:41-47) ↔ admin_search.py:11,42-44 ✓; `SearchConfigRepository.{list_all,get,upsert,set_health,set_key,set_enabled}` все вызываемые существуют ✓; `app.state.search_manager` ставится (`api/app.py:44`) и передаётся в GenerationService (`main.py:75,89`) ✓; `_split_text`/`MESSAGE_LIMIT`/`new_draft_id` импорт из draft.py существуют ✓; фичи `_BACKEND_FACTORIES` принимают `api_key` (Playwright игнорирует) ✓; A25-модель-денайл до `registry.get` (generation.py:1013-1020) ✓; A39-фикс 0be0524 на месте: `round_calls = outcome.tool_calls[:max_tool_calls_per_round]` ДО `_assistant_tool_message` (generation.py:761-765) — orphan tool_calls больше не создаётся.
2. **SSRF ядро (A19):** egress-запрет полный (`ssrf.py:20-25`: metadata, multicast/reserved/unspecified/not is_global; IPv6 `::1`, `fe80::`, `fc00::`, IPv4-mapped блокируются — тесты test_ssrf.py:39-58); pinned connect per hop (вкл. редиректы — `test_fetch_pinned_per_redirect_hop`), DNS при connect не перезапрашивается; `trust_env=False` и `follow_redirects=False` (fetcher.py:236-241); TLS verify не отключён, SNI/cert по реальному хосту. Порт нестандартный разрешён осознанно (документировано), connect идёт к проверенному IP с портом из URL.
3. **Redirect-политика:** максимум 5 редиректов (6 hop'ов) → «too many redirects» (симуляция: redirect-loop → SearchBackendError, exit clean); redirect-into-private блокируется ДО второго запроса (тест, calls==1); `data:`/`//host`/относительные Location корректно проходят urljoin+revalidate.
4. **Decompression-бомба: закрыта.** `_read_body` (fetcher.py:129-139) читает через `response.aiter_bytes` — это ДЕКОМПРЕССИРОВАННЫЕ байты (httpx применяет Content-Encoding при aiter_bytes), лимит max_bytes=2 МБ применяется к декодированному телу. Симуляция: gzip 19 466 байт → 20 МБ декомпрессии при max_bytes=1 МБ → `len(text)==1_000_000, truncated=True`, память ок (инкрементальный decode по чанкам).
5. **Jina reader получает только SSRF-проверенный URL:** `_open_url_handler` → `fetch_url` → validate (каждый hop) → `_extract_text(raw.final_url, …, reader)` (fetcher.py:245-260) — reader не может получить непроверенный URL; ошибки/None reader'а → локальная экстракция проверенного тела (тесты ×3). Reader-инстанс только через `manager.get_reader` (единственный consumer — generation.py:758).
6. **Tool engine:** unknown tool при allowed set → `denied` ДО registry-lookup (runner.py:65-70, тест); permission re-check в execute (runner.py:81-84); cancellation перед каждым side-effect (generation.py:766-769) + интеграционный тест; `asyncio.wait_for` таймаут (runner.py:126-129, тест timeout/транкейт 418/421-428); неожиданные исключения хендлера → санитизированный «Tool error» (детали только в лог); ToolExecutionError-текст модели — контролируемый. `allowed_tool_names=frozenset(...)` — пустой frozenset при выключенных tools → fail-closed.
7. **Search backends / manager:** isinstance-валидация во всех парсерах (serper/brave/serpapi/jina/playwright) → SearchBackendError → fallback к следующему (manager._normal_chain); 401/403→Unavailable, 429/5xx/timeout→1 повтор (base.py:113-152); playwright challenge/429/503 → BackendUnavailable → cooldown 15 мин (in-memory, experimental — документировано, CAPTCHA-bypass/stealth отсутствуют — код прочитан); `_dedupe` по url; auto-режим normal-first (manager.py:203-220); AIO без references не авторитетен (serpapi:80-83 + manager:239-242). Serper/brave/serpapi `num`/`count`/slicing по max_results корректны; Brave description → strip_html.
8. **Crypto:** Fernet authenticated; `decrypt` мусор → ValueError → backend не configured (fail-closed, manager.py:87-94); `mask_secret` — head/tail 4+4, короткие → `***`; `key_hint` = последние 4 симв. — согласовано с mask-политикой; секретов в логах модуля нет. `search_backend_configs`-стабы из `list_backends` создаются disabled (model default enabled=False, priority=100) — авто-включения нет.
9. **SQL/FTS:** `search_fts` — `plainto_tsquery` с bound-параметром через SQLAlchemy (memories.py:105-122) — инъекций нет.
10. **`web_search` no-retry hint** (builtin.py:117-125): имя-эвристика `SearchBackendError` работает для manager-уровня (manager поднимает только плоский SearchBackendError — BackendUnavailableError гасится внутри цепочки); BackendUnavailableError извне manager не пробивается.

---

## Сводная таблица

| # | Находка | Серьёзность | Файлы:строки | Доказательство |
|---|---|---|---|---|
| 1 | A32-регрессия 0be0524: «Источники» теряют заголовки — title==url в каждом ответе с web_search | **P1** | builtin.py:141-150; generation.py:217-253,473-477 | Симуляция реального формата: footer `1. [https://docs.python.org/3/](https://docs.python.org/3/)`; старый формат давал `[Python docs](…)`. Тесты тестируют старый порядок |
| 2 | Truncation посреди SOURCES-строки → битый частичный URL с «… [truncated]» в пользовательских «Источниках» и tool-результате | P2 | runner.py:148-150; registry.py:33; builtin.py:141-144; generation.py:226-230 | Симуляция 10×300-симв. URL: last source `'https://example1.com/ppp… [truncated]'` |
| 3 | IPv6-литерал: Host без скобок (RFC 7230), с портом — `'2001:db8::1:8080'`; httpx сам поставил бы корректный | P2 | fetcher.py:53-74; ssrf.py:63-71 | Офлайн-проба `_pin_request`: Host `'2001:db8::1'`/`'2001:db8::1:8080'` vs httpx-default `'[2001:db8::1]'` |
| 4 | chat title интерполируется в сообщение с default parse_mode=HTML: TelegramBadRequest / HTML-разметка-линки | P2 | commands.py:140; dispatcher.py:38-42; chats.py:24-33,122-124; titles.py:34-47 | Единственный answer() без parse_mode=None; API-title без санитизации (max_length=256) |
| 5 | httpx.InvalidURL (невалидный Location редиректа, напр. `data:…`) уходит из классификации «httpx-ошибки → SearchBackendError» → generic «Tool error» | P3 | fetcher.py:150-171 | Трейсбек: InvalidURL не HTTPError-сабкласс; пробрасывается из `_single_hop` |
| 6 | Бюджеты: reader 8000 / html_to_text 20000 > tool max_result_size 4000 | P3 | jina.py:123; reader.py:75; registry.py:33; builtin.py:277-286 | Чтение кода; всё сверх 4000 срезается в runner |
| 7 | Reader X-Timeout 30 с при tool-бюджете open_url 30 с (fallback не успевает) | P3 | jina.py:122; builtin.py:283; fetcher.py:178 | Сумма таймаутов > бюджета |
| 8 | ToolContext.settings = env-снимок (memory_dedup_threshold), не effective (A13) | P3 | generation.py:751-759 vs 998-1009 | Сверка путей effective/env |
| 9 | aclose инстанса при смене конфига может оборвать in-flight поиск (транзиент) | P3 | manager.py:105-117 | lifecycle-lock не защищает in-flight |
| 10 | P0-класс (runtime имена/сигнатуры по scope) | **не найдено** | mypy 18 файлов: 0 issues; ручная сверка manager/routes/repo | Все методы/поля существуют |
| 11 | SSRF ядро (pinned, egress, redirects ≤5, data:-блок, бомба 2 МБ post-decompress) | OK | ssrf.py; fetcher.py | 132+164 теста зелёные + симуляции |

**Итог:** новый P0 (runtime-crash) в scope не найден; главная находка — семантическая регрессия P1 от 0be0524 (A32): переезд SOURCES в голову сломал заголовки источников у ВСЕХ поисковых ответов, плюс три P2 (битые URL из truncation, IPv6 Host, HTML-инъекция в title-сообщение). Все SSRF-инварианты (A19: pin/SNI/verify/redirects/limits, анти-бомба) подтверждены; tool engine fail-closed; crypto OK.
