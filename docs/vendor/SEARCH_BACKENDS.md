# Search backends — vendor doc

- **Дата проверки:** 2026-09-18
- **Источники:** serper.dev, serpapi.com (google-ai-overview-api, ai-overview), api-dashboard.search.brave.com/documentation, jina.ai/reader. Полный отчёт: `.agents/reports/search.md`.

## Модель данных проекта

`SearchResult{title, url, snippet(plain text), source(str|None), published_at(str|None), metadata{...}}`
Режимы web_search: `normal` | `ai_overview` | `auto` (auto: эвристика → ai_overview, деградация в normal без ошибки).

## Бэкенды (в порядке fallback проекта)

### 1. Serper (PRIMARY)
- `POST https://google.serper.dev/search`, header `X-API-KEY`, body `{q, gl, hl, num, page}`.
- Ответ: `organic[]: {title, link, snippet, date?, position}` (+knowledgeGraph/peopleAlsoAsk).
- Mapping: link→url, date→published_at (свободный текст, опционально).
- Квоты: top-up кредиты ($1.0–0.3/1k), 2500 бесплатно, 50–300 QPS, 1–2 с латентность, без кэша.
- ⚠️ **Пробел:** официальные docs-страницы недоступны из dev-среды; формат запроса — de-facto стандарт, верифицировать после регистрации ключа.

### 2. SerpApi Google AI Overview (режим ai_overview, НЕ замена обычного поиска)
- Шаг 1: `GET https://serpapi.com/search.json?engine=google&q=...&api_key=...` → `ai_overview` содержит либо `text_blocks`+`references`, либо `page_token`(+serpapi_link), либо `error`.
- Шаг 2 (если page_token): `GET ...?engine=google_ai_overview&page_token=...&api_key=...` — **TTL токена 1 мин** (docs противоречат: 1 vs 4 мин → используем ≤60 с).
- Схема: `text_blocks[]{type,snippet,reference_indexes[]}`, `references[]{title,link,snippet,source,index}`; `output=md` — markdown-вариант.
- Mapping: overview_text ← text_blocks (в metadata, НЕ как источник истины); citations ← references.
- Цена: 2 списания за двухшаговый флоу; 250 бесплатно/мес. AI Overview есть не для всех запросов/локалей (RU — эмпирически проверить) → fallback в normal.

### 3. Brave Search (optional)
- `GET https://api.search.brave.com/res/v1/web/search`, header `X-Subscription-Token`, `Accept: application/json`.
- Параметры: `q, count(≤20), offset(≤9), country, search_lang, freshness(pd/pw/pm/py), safesearch, extra_snippets`.
- Ответ: `web.results[]{title,url,description(HTML <strong> — strip!),age?,page_age?(ISO),profile{...}}`.
- Mapping: description→snippet (strip tags), page_age→published_at (лучший источник даты).
- Квоты: $5/1000 запросов, $5 бесплатных кредитов/мес; **нужна карта даже для free**; 50 QPS.
- Без ключа — backend disabled (не падаем).

### 4. Jina (s.jina.ai search / r.jina.ai reader)
- Search: `GET https://s.jina.ai/?q=...` — топ-5 с полным контентом; **ключ обязателен**; ≥10k токенов/запрос; free key 100 RPM.
- Reader: `GET https://r.jina.ai/<url>` — URL→Markdown/JSON (`Accept: application/json`); без ключа 20 RPM, free key 500 RPM; заголовки `X-Timeout`, `X-Token-Budget`, `X-Engine: direct` (HTTP без рендеринга).
- ⚠️ **Пробел:** auth-заголовок `Authorization: Bearer` дословно не подтверждён в сессии — проверить на живом ключе.
- Проектное применение: Reader — один из экстракторов в `open_url` (наряду с direct HTTP); s.jina.ai — tertiary fallback поиска.

### 5. Playwright Google (EXPERIMENTAL, disabled by default)
- Локальный Chromium, обычный SERP парсинг + попытка AI Overview.
- **Запрещено:** обход CAPTCHA, stealth-спуфинг, решение challenge, логин в Google.
- При CAPTCHA (`/sorry/`, recaptcha) → abort + cooldown бэкенда + следующий SearchBackend.

## Общие решения проекта

- Таймауты httpx: connect 5 с; read: search 10 с, ai_overview/reader 30 с; общий deadline цепочки ~40 с.
- Ретраи: только 429/5xx/timeout (1–2 попытки, exp backoff); 401/403 → mark backend unhealthy → fallback.
- Fallback при: 401/402/403/429, 5xx после ретраев, timeout, пустой organic. «0 результатов» как валидный ответ — НЕ fallback-повод.
- Конфиг в БД (search_backend_configs): enabled, priority, encrypted key, health; редактируется в админке, Test query.
- Дедуп citations по URL при merge.
