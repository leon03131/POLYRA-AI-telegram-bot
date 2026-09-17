# R-SEARCH: исследование бэкендов web_search

**Дата проверки:** 2026-09-18
**Задача:** точные HTTP-детали для реализации tool `web_search` (httpx) с абстракцией `SearchBackend` и приоритетным fallback.

## Источники и статус доступности

| URL | Статус | Что получено |
|---|---|---|
| https://serper.dev/ | OK | Примеры JSON-ответов всех endpoint'ов, прайсинг, FAQ |
| https://serper.dev/docs | 404 | — |
| https://serper.dev/documentation | 404 | — |
| https://serper.dev/api-reference | 404 | — |
| https://docs.serper.dev | Transport error (не резолвится) | — |
| https://app.serper.dev/documentation | Transport error | — |
| https://serper.dev/playground | OK, но пусто (JS-приложение, cookie-wall) | — |
| https://serpapi.com/ | OK | Прайсинг, список engine'ов |
| https://serpapi.com/google-ai-overview-api | OK | Параметры engine `google_ai_overview` |
| https://serpapi.com/ai-overview | OK | Полная JSON-схема `ai_overview`, примеры, двухшаговый флоу |
| https://docs.serpapi.com/ | Transport error | — (документация живёт на самом serpapi.com) |
| https://brave.com/search/api/ | OK | Примеры запросов/ответов, прайсинг, FAQ |
| https://docs.search.brave.com | Transport error (не резолвится) | — |
| https://api-dashboard.search.brave.com/app/documentation | OK (редирект на /documentation) | Структура документации |
| https://api-dashboard.search.brave.com/documentation/services/web-search | OK | Параметры Web Search: freshness, pagination, safesearch, extra_snippets |
| https://jina.ai/ | OK | Навигация, форма параметров Reader |
| https://jina.ai/reader | OK | Полная документация Reader/Search, rate limits, FAQ |

---

## 1. Serper (primary)

**Статус верификации:** схемы ответов и прайсинг подтверждены примерами на главной странице; формат запроса из официальной документации **не подтверждён** (все docs-страницы 404/недоступны) — см. «Пробелы».

### Endpoint / запрос (de-facto стандарт, НЕ верифицирован в этой сессии)

- `POST https://google.serper.dev/search`
- Заголовки: `X-API-KEY: <key>`, `Content-Type: application/json`
- Тело JSON: `q` (обяз.), `gl` (страна, напр. `"us"`), `hl` (язык, напр. `"en"`), `num` (число результатов, default 10), `page` (страница пагинации)
- Другие endpoint'ы (тот же хост/метод/заголовок, другой путь): `/images`, `/news`, `/videos`, `/places`, `/maps`, `/shopping`, `/scholar`, `/patents`, `/autocomplete` — подтверждено наличием соответствующих примеров на главной странице.

### Схема ответа (верифицировано примерами на serper.dev)

`/search`:
```json
{
  "knowledgeGraph": { "title": "...", "type": "...", "website": "...", "description": "...", "attributes": {} },
  "organic": [
    {
      "title": "...",
      "link": "https://...",
      "snippet": "...",
      "date": "Mar 10, 2022",        // опционально, не у всех результатов
      "sitelinks": [{"title": "...", "link": "..."}],  // опционально
      "attributes": {"...": "..."},  // опционально
      "position": 1
    }
  ],
  "peopleAlsoAsk": [{"question": "...", "snippet": "...", "title": "...", "link": "..."}],
  "relatedSearches": [{"query": "..."}]
}
```
- `/news`: `news[]`: `title`, `link`, `snippet`, `date` (строка вида `"2 weeks ago"`), `source`, `imageUrl`, `position`.
- `/images`: `images[]`: `title`, `imageUrl`, `imageWidth/Height`, `thumbnailUrl`, `source`, `domain`, `link`, `googleUrl`, `position`.
- `/videos`: `videos[]`: `title`, `link`, `snippet`, `imageUrl`, `duration`, `source`, `channel`, `date`, `position`.
- `/autocomplete`: `suggestions[]`: `value`.

**Mapping в SearchResult:** `title`←`title`, `url`←`link`, `snippet`←`snippet`, `published_at`←`date` (не всегда присутствует; формат свободный — `"Mar 10, 2022"` или `"2 weeks ago"`).

### Лимиты / квоты (верифицировано)

- Модель: top-up кредиты, не подписка. $50 → 50k кредитов ($1.00/1k), $375 → 500k ($0.75/1k), $1250 → 2.5M ($0.50/1k), $3750 → 12.5M ($0.30/1k). Кредиты действуют 6 месяцев.
- Бесплатно при регистрации: 2500 запросов (no credit card).
- Rate limit по плану: 50 / 100 / 200 / 300 QPS.
- Латентность: обычно 1–2 с; при ретрае на стороне Serper до 2–4 с.
- Результаты **не кэшируются** (всегда real-time). Кредит списывается только за успешные ответы.

### Особенности

- `gl`/`hl` задают геолокацию и язык — подходит для RU-локали (`gl: "ru"`, `hl: "ru"` — значения не верифицированы, стандартные ISO-коды).
- Ответ компактный, идеально ложится на `SearchResult`.

---

## 2. SerpApi Google AI Overview (отдельный режим)

**Статус верификации:** флоу и схема подтверждены официальной документацией (serpapi.com/google-ai-overview-api, serpapi.com/ai-overview).

### Точный флоу получения AI Overview

**Шаг 1.** Обычный запрос Google Search API:
```
GET https://serpapi.com/search.json?engine=google&q=<query>&api_key=<KEY>
```
В ответе блок `ai_overview` бывает в двух состояниях:
- **(a) Готовый:** сразу содержит `text_blocks` + `references` (структура ниже).
- **(b) Требуется доп. запрос:** содержит только `page_token` и `serpapi_link`:
```json
{ "ai_overview": { "page_token": "<long token>", "serpapi_link": "https://serpapi.com/search.json?engine=google_ai_overview&page_token=..." } }
```
- **(c) Ошибка:** `{ "ai_overview": { "error": "Can't generate an AI overview right now. Try again later." } }` — Google не всегда генерирует AI Overview; это ожидаемое поведение, не ошибка клиента.

**Шаг 2 (только для случая b).**
```
GET https://serpapi.com/search?engine=google_ai_overview&page_token=<TOKEN>&api_key=<KEY>
```
`page_token` — единственный обязательный параметр engine (кроме `engine` и `api_key`). Полученный JSON имеет ту же структуру `ai_overview`, что и в случае (a).

⚠️ **Противоречие в документации SerpApi по TTL токена:** страница `google-ai-overview-api` пишет «expires within **1 minute**», страница `ai-overview` — «expire within **4 minutes**». Рекомендация: использовать токен немедленно (≤60 с).

### Схема ответа `ai_overview` (верифицировано, «JSON structure overview»)

```json
{
  "ai_overview": {
    "text_blocks": [
      {
        "type": "heading | paragraph | list | expandable | comparison | table | top_stories",
        "snippet": "...",
        "snippet_highlighted_words": ["..."],
        "snippet_latex": ["..."],
        "snippet_links": [{"text": "...", "link": "..."}],
        "reference_indexes": [0, 4, 7],
        "list": [{"title": "...", "link": "...", "snippet": "...", "reference_indexes": [...], "list": [ /* вложенные */ ]}],
        "table": [["...", "..."]],
        "text_blocks": [ /* рекурсивно, для expandable */ ],
        "comparison": [{"feature": "...", "values": ["...", "..."]}],
        "video": {"link": "...", "thumbnail": "...", "source": "...", "date": "..."}
      }
    ],
    "products": [{"thumbnail": "...", "title": "...", "rating": "...", "price": "...", "extracted_price": 0}],
    "top_stories": [{"title": "...", "link": "...", "source": "...", "live": true, "date": "2 hours ago", "thumbnail": "..."}],
    "header_images": [{"image": "...", "source": "..."}],
    "thumbnail": "...",
    "references": [
      {"title": "...", "link": "https://...", "snippet": "...", "source": "Shopify", "index": 0}
    ],
    "error": "..."
  }
}
```

**Mapping в SearchResult (режим ai_overview):**
- Сам обзор: конкатенация `text_blocks[].snippet` (по типам paragraph/heading/list) → в отдельное поле (напр. `metadata.ai_overview_text`).
- Citations: `references[]` → SearchResult: `title`←`title`, `url`←`link`, `snippet`←`snippet`, `source`←`source`, `published_at`←нет (отсутствует; дата иногда в начале `snippet`, напр. `"May 22, 2024 — ..."`).
- Связь текст↔источники: `reference_indexes` указывают на `references[index]`.

### Параметры engine `google_ai_overview` (верифицировано)

| Параметр | Обяз. | Описание |
|---|---|---|
| `engine` | да | `google_ai_overview` |
| `page_token` | да | токен из шага 1 |
| `api_key` | да | приватный ключ |
| `output` | нет | `json` (default) / `html` / `md` (markdown для LLM) |
| `no_cache` | нет | кэш 1 ч; закэшированные запросы бесплатны |
| `async` | нет | асинхронная выдача через Searches Archive API |
| `json_restrictor` | нет | фильтрация полей ответа |

### Прайсинг (кратко, верифицировано)

- Бесплатный план: 250 поисков/мес, 50 запросов/час.
- Платно от $25/мес (1000 поисков, 200/час) и выше.
- Списываются только успешные запросы; кэш бесплатен. Двухшаговый флоу = 2 списания (поиск + AI Overview), если шаг 2 не попал в кэш.
- Есть также отдельный `engine=google_ai_mode` (Google AI Mode API) — на будущее.

### Особенности

- AI Overview генерируется Google не для всех запросов/локалей — нужен fallback на обычный organic (`engine=google` → `organic_results`).
- SerpApi сам решает CAPTCHA (полный browser cluster), поэтому надёжность выше самодельного Playwright.

---

## 3. Brave Search API (optional)

**Статус верификации:** подтверждено документацией (api-dashboard.search.brave.com/documentation) и примерами на brave.com/search/api.

### Endpoint / запрос

- `GET https://api.search.brave.com/res/v1/web/search`
- Заголовки: `X-Subscription-Token: <KEY>`, `Accept: application/json`, `Accept-Encoding: gzip`
- Документация: `https://api-dashboard.search.brave.com/documentation` (НЕ docs.search.brave.com — не резолвится).

### Ключевые параметры (верифицировано)

| Параметр | Описание |
|---|---|
| `q` | запрос (обяз.) |
| `count` | результатов на страницу, **max 20**, default 20 |
| `offset` | сдвиг, 0-based, **max 9** (т.е. максимум ~200 результатов) |
| `country` | 2-буквенный код (напр. `US`, `DE`) |
| `search_lang` | язык контента (ISO 639-1, напр. `en`, `de`) |
| `ui_lang` | язык метаданных ответа (напр. `en-US`) |
| `freshness` | `pd` (24 ч) / `pw` (7 дн.) / `pm` (31 дн.) / `py` (год) / диапазон `2022-04-01to2022-07-30` |
| `safesearch` | `off` / `moderate` (default) / `strict` |
| `extra_snippets` | `true` → до 5 доп. сниппетов на результат |
| `spellcheck` | `1`/`0` |
| `goggles` | кастомное реранжирование |
| `enable_rich_callback` | `1` → rich-данные (погода, акции...) через отдельный callback-запрос |
| В `q` поддерживаются операторы: `"фраза"`, `-исключение`, `site:`, `filetype:` | |

### Схема ответа (верифицировано примерами)

```json
{
  "type": "search",
  "query": {"original": "...", "more_results_available": true},
  "web": {
    "type": "search",
    "results": [
      {
        "title": "...",
        "url": "https://...",
        "description": "сниппет с <strong>подсветкой</strong>",
        "age": "1 day ago",
        "page_age": "2025-04-01T13:20:42",
        "page_fetched": "2025-02-28T23:32:43Z",
        "profile": {"name": "Tripadvisor", "url": "...", "long_name": "tripadvisor.com", "img": "<favicon>"},
        "meta_url": {"scheme": "https", "netloc": "...", "hostname": "...", "favicon": "...", "path": "..."},
        "is_source_local": false,
        "is_source_both": false,
        "extra_snippets": ["..."]
      }
    ]
  }
}
```
(поля `age`/`page_age`/`page_fetched` опциональны; `page_age`/`page_fetched` — ISO-даты, `age` — человекочитаемая строка)

**Mapping в SearchResult:** `title`←`title`, `url`←`url`, `snippet`←`description` (содержит HTML-теги `<strong>` — чистить), `published_at`←`page_age` (ISO, предпочтительно) или `page_fetched` / парсинг `age`.

### Квоты / прайсинг (верифицировано)

- Модель с 2025+: кредитная. Search: **$5 за 1000 запросов**, 50 QPS. **$5 бесплатных кредитов ежемесячно** (~1000 запросов/мес фактического free tier).
- Для регистрации (включая free) **требуется банковская карта** (антифрод; не списывается) — из FAQ.
- Answers API (OpenAI-совместимый chat/completions, модель `brave`): $4/1000 запросов + $5/1M токенов, 2 QPS.
- LLM Context endpoint `GET /res/v1/llm/context?q=...`: `grounding.generic[]: {url, title, snippets[]}` + `sources{<url>: {title, hostname, age:[human, ISO, relative]}}` — оптимизирован под RAG, до 5 сниппетов на URL.
- Хранение результатов требует плана с storage rights (иначе ToS запрещает).

### Особенности

- Собственный независимый индекс (30+ млрд страниц), не обёртка над Google — устойчив к изменениям Google SERP.
- Есть endpoint'ы: `/news/search`, `/images/search`, `/videos/search`, `/suggest/search`, `/spellcheck/search`, `/local/pois`.
- Brave позиционирует LLM Context как endpoint «для агентов», а web/search — «для людей»; для citations достаточно web/search.

---

## 4. Jina Reader / Search (s.jina.ai, r.jina.ai)

**Статус верификации:** подтверждено jina.ai/reader (документация + таблица rate limits + FAQ).

### Reader (`r.jina.ai`) — чтение URL

- `GET https://r.jina.ai/<полный URL>` (напр. `https://r.jina.ai/https://example.com`), также `POST`.
- Auth: API-ключ в заголовке запроса (на странице — «provide an API key in the request header»; формат `Authorization: Bearer <key>` — общеизвестный, в этой сессии дословно не показан, см. «Пробелы»). Без ключа тоже работает с низким лимитом.
- Ключевые заголовки (верифицировано формой на сайте):
  - `Accept: application/json` → JSON `{url, title, content, timestamp}` (в Search-режиме — список из 5 таких записей)
  - `Accept: text/event-stream` → stream-режим (для больших/медленных страниц)
  - `X-Timeout` (сек), `X-Token-Budget`, `X-Engine` (`direct` — быстрый HTTP-fetch без рендеринга; default — headless-браузер; `cf-browser-rendering` — экспериментальный), `X-Target-Selector` / `X-Remove-Selector` (CSS), `X-Wait-For-Selector`, `X-Respond-With` (напр. `readerlm-v2` для структурированной выдачи), `X-No-Cache`, `X-Cache-Tolerance`, `X-Locale`, `X-With-Links-Summary`, `X-With-Images-Summary`, `X-Retain-Links` (формат цитат OpenAI).
- Default-ответ без `Accept: application/json` — plain Markdown: `Title: ...\nURL Source: ...\nMarkdown Content:\n...` (формат title/url виден в документации косвенно; детально не верифицирован в этой сессии).
- Кэш: повтор того же URL в течение 5 минут отдаётся из кэша.
- Не обходит антибот-защиту («If a website blocks the request, that outcome is respected»).

### Search (`s.jina.ai`) — поиск + контент

- `GET https://s.jina.ai/?q=<urlencoded query>` (также форма `https://s.jina.ai/<query>`), также `POST`.
- Возвращает **топ-5 результатов**, каждый с URL и полным контентом страницы (Reader-пайплайн), в Markdown или JSON (при `Accept: application/json`: список из 5 записей `{url, title, content, timestamp}`).
- **Обязателен API-ключ** (без ключа endpoint заблокирован — «block» в таблице).
- Каждый запрос стоит фиксированно **от 10000 токенов** (тарификация за токены, не за запросы).

### Rate limits (верифицировано таблицей на jina.ai/reader, 2026-09)

| Endpoint | Без ключа | Free key | Paid key | Premium | Ср. латентность |
|---|---|---|---|---|---|
| `r.jina.ai` | 20 RPM | 500 RPM | 500 RPM | 5000 RPM | 7.9 с |
| `s.jina.ai` | блок | 100 RPM | 100 RPM | 1000 RPM | 2.5 с |

- Общие лимиты по ключу: Free 100 RPM / 100K TPM; Paid 500 RPM / 2M TPM; Premium 5000 RPM / 50M TPM. Плюс IP-лимит 10000 req/60 с.
- Новый ключ: **10M бесплатных токенов**. Неуспешные запросы токены не списывают. Ключи не истекают.
- Лимит срабатывает по RPM **или** TPM (что раньше); с ключом — по ключу, без ключа — по IP.

### Reader vs Search: когда что

- **`s.jina.ai`** — когда есть только запрос и нужны готовые «заземляющие» тексты: 1 запрос → 5 страниц с полным контентом. Дорого по токенам (≥10k/запрос), но просто; хорош как аварийный fallback без парсинга.
- **`r.jina.ai`** — когда URL уже известны (из Serper/Brave): дочитать топ-N страниц в Markdown/JSON для глубокого ответа. Дешевле (токены = объём выдачи), управляемее (`X-Token-Budget`).
- Классическая связка: search-бэкенд (Serper) → `r.jina.ai` для топ-3 URL.

---

## 5. Экспериментальный Playwright Google

Исследование вне scope web-проверки (локальный компонент). Зафиксированные проектные требования: **без обхода CAPTCHA** — при получении CAPTCHA/блока считать бэкенд недоступным и падать в fallback. Рекомендации по реализации: headless Chromium, human-like UA, `google.com/search?q=...&hl=...&gl=...&num=...`, парсинг organic-блоков, жёсткий timeout, детект CAPTCHA по сигнатурам (`/sorry/`, `recaptcha`), лимит частоты (self-throttle). Это бэкенд последнего резерва / dev-режима без ключей.

---

## Общие практики (таймауты, поля для citations)

- **Таймауты (httpx):** connect 5 с; read зависит от бэкенда по измеренным латентностям: Serper ~4 с (обычно 1–2 с), Brave ~5 с, SerpApi ~15–30 с (full browser, второй шаг AI Overview отдельно), Jina s.jina.ai ~10 с (avg 2.5 с), Jina r.jina.ai ~20 с (avg 7.9 с, SPA дольше). Рекомендуемый общий read-timeout по умолчанию 10 с для search-запросов, 30 с для reader/ai_overview; общий deadline на tool-вызов с fallback-цепочкой ~35–40 с.
- **Поля для citations (достаточно):** `title`, `url`, `snippet`, `published_at` (опц.). Источник/домен (`source`) — желательно для отображения.
- **Ретраи:** только на 429/5xx/таймаут, 1–2 попытки с экспоненциальной паузой; на 401/403 — не ретраить, помечать бэкенд недоступным (это триггер fallback).
- **Даты:** форматы у всех разные — Serper `date` свободный текст (опц.), Brave `page_age` ISO (лучший), SerpApi references без даты, Jina — `timestamp` опц. Нормализовать в `published_at: str | None`, не гарантировать наличие.
- **HTML в сниппетах:** Brave `description` содержит `<strong>` — strip-теги перед выдачей.

---

## Противоречия и пробелы

1. **Serper: формат запроса не верифицирован из официального источника.** Все docs-URL (serper.dev/docs, /documentation, /api-reference, docs.serper.dev, app.serper.dev/documentation) недоступны (404/transport error). Endpoint `POST https://google.serper.dev/search` + `X-API-KEY` + `{q, gl, hl, num, page}` — общеизвестный стандарт (подтверждается косвенно: поля `gl`/`hl` видны в UI-примерах главной страницы, response-схемы верифицированы), но требует верификации после регистрации (документация, вероятно, внутри dashboard).
2. **SerpApi: TTL `page_token`** — 1 минута (страница google-ai-overview-api) vs 4 минуты (страница ai-overview). Брать минимум.
3. **Jina: имя auth-заголовка** (`Authorization: Bearer`) в этой сессии дословно не показано на странице (только «API key in the request header»). Вероятность высокая, но проверить при первом запуске; также есть альтернатива `X-No-Cache` и др. — документированы.
4. **Brave: бесплатный tier** — формально это «$5 бесплатных кредитов/мес», а не отдельный free-план; для получения нужна банковская карта. Ранее существовавший free plan (2000 запросов/мес) на странице не упоминается — зафиксировать изменение модели.
5. **Jina s.jina.ai:** максимум 5 результатов, нет параметров локали/пагинации в публичном описании — ограничение как основного поискового бэкенда.
6. **SerpApi AI Overview доступность:** Google генерирует обзор не для всех запросов/регионов; RU-локаль под вопросом (в примерах документации только en/US). Требуется эмпирическая проверка.

---

## Рекомендации проекту

### Модель данных

```python
SearchResult = {
    "title": str,
    "url": str,
    "snippet": str,  # plain text, HTML stripped
    "source": str
    | None,  # домен/издатель (Brave: profile.name/meta_url.hostname; Serper: домен из link; SerpApi: references[].source)
    "published_at": str | None,  # ISO если возможно (Brave page_age), иначе сырой текст, иначе None
    "metadata": dict,  # position, extra_snippets, reference_indexes, ai_overview_text, raw (опц.)
}
```

### Порядок fallback (mode = normal)

1. **Serper** (primary): дешёвый ($0.3–1.0/1k), быстрый (1–2 с), точный Google SERP. Требует верификации запроса (пробел №1).
2. **Brave** (secondary, optional): независимый индекс, устойчив, $5 кредитов/мес бесплатно; включать если есть ключ.
3. **Jina s.jina.ai** (tertiary): без парсинга (сразу контент), но 5 результатов и ≥10k токенов/запрос — годится как «спасательный круг», бесплатный ключ с 10M токенов покрывает ~1000 поисков.
4. **Playwright Google** (experimental, last resort / offline dev): без обхода CAPTCHA.

Правила fallback: переход при HTTP 401/402/403/429, 5xx после ретраев, timeout, пустой organic. Не переходить при «0 результатов от Google» как валидном ответе (Serper без кэша — редко).

### Режимы

- `normal` — цепочка выше, возврат `SearchResult[]`.
- `ai_overview` — только SerpApi: шаг 1 `engine=google` (заодно получаем organic как бонус) → если `ai_overview.text_blocks` есть — готово; если `page_token` — немедленно шаг 2 `engine=google_ai_overview` (TTL ≤60 с!); если `error`/отсутствует — fallback на `normal`-выдачу с пометкой. Ответ: `{"overview_text": ..., "results": [SearchResult из references]}`.
- `auto` — эвристика: запрос вопросительный/аналитический → `ai_overview`, иначе `normal`; при сбое ai_overview-флоу — деградация в `normal` без ошибки.

### Дочитка контента (опциональный второй этап tool)

Отдельный вызов/параметр `fetch_content: bool`: топ-N URL → `r.jina.ai` (Accept: application/json, X-Timeout ~20). Не смешивать с поиском в один HTTP-вызов.

### Конфиг

`SEARCH_BACKENDS_ORDER`, ключи `SERPER_API_KEY` / `SERPAPI_API_KEY` / `BRAVE_SEARCH_API_KEY` / `JINA_API_KEY`, per-backend enable-флаги, `SEARCH_DEFAULT_MODE=auto`, таймауты из раздела «Общие практики», дефолтная локаль `gl/country` + `hl/search_lang` из настроек бота.

---

## Открытые вопросы

1. Подтвердить формат запроса Serper после регистрации (docs внутри dashboard): точные имена параметров, лимит `num` (максимум), поведение `page`.
2. Эмпирически проверить: генерирует ли Google AI Overview для RU-запросов через SerpApi (локаль/язык), и фактический TTL `page_token`.
3. Проверить фактический auth-заголовок Jina (`Authorization: Bearer`) и JSON-схему s.jina.ai на живом ключе.
4. Уточнить условия хранения результатов Brave (storage rights) — влияет на кэширование ответов в проекте.
5. Решить, нужен ли SerpApi `engine=google_ai_mode` (Google AI Mode) вместо/вдобавок к AI Overview в будущем.
6. Оценить стоимость режима `ai_overview` (2 списания SerpApi за запрос) на ожидаемой нагрузке бота.
7. Определить политику дедупликации citations при merge результатов разных бэкендов (по URL?).
