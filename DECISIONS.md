# DECISIONS — архитектурный журнал (ADR)

Формат: ID | Дата | Статус | Решение | Контекст/причина.

## ADR-001 | 2026-09-18 | accepted
Telegram updates: **long polling по умолчанию**, webhook — optional режим позже.
Причина: проще с текущей network configuration; Mini App всё равно требует HTTPS reverse proxy.

## ADR-002 | 2026-09-18 | accepted
Без Redis. Locks/state/persistence — PostgreSQL (в т.ч. advisory locks для
«одна активная генерация на чат»). Причина: один владелец + немного приглашённых.

## ADR-003 | 2026-09-18 | accepted
Gemini ходит ТОЛЬКО через proxy владельца `extraordinary-piroshki-4e3b92.netlify.app`.
Сначала google-genai `HttpOptions/base_url`; если Interactions API через SDK ломается —
аккуратный raw HTTP adapter (httpx) через тот же proxy. Прямой Google endpoint не подставляем.

## ADR-004 | 2026-09-18 | accepted
Alibaba: ТОЛЬКО `dashscope.aliyuncs.com/compatible-mode/v1`, OpenAI SDK/httpx,
клиент с `trust_env=false` (не подхватывать HTTP(S)_PROXY). Endpoint read-only в UI.
Без proactive RPM/TPM/RPD квот; обрабатываем 429/5xx/timeout/auth errors реактивно.

## ADR-005 | 2026-09-18 | accepted
**Никакого cross-model fallback.** Failover только внутри GeminiProjectPool между
проектами/ключами для ТОЙ ЖЕ модели (400 — не ротируем; 401 — disable+next;
403 — cooldown+next; 429 — cooldown project+model + next; 5xx/timeout — bounded retry + next;
safety — не ротируем). Максимум один полный проход пула.

## ADR-006 | 2026-09-18 | accepted
Reasoning (reasoning_content/thinking) никогда не стримится и не показывается
пользователю, не пишется в messages. В БД — только reasoning token count, если отдан.
Internal subagent-модель: gemini-3.5-flash-lite (summary=MEDIUM, memory=MEDIUM, title=LOW).

## ADR-007 | 2026-09-18 | accepted
Mini App auth: frontend шлёт RAW `Telegram.WebApp.initData`; backend валидирует
HMAC-SHA256 (secret=HMAC("WebAppData", bot_token)) + auth_date freshness + access;
далее выдаёт короткоживущий server session token (подписанный, с exp).
Admin endpoints: только user_id == 795063564. Username — никогда не identity.

## ADR-008 | 2026-09-18 | accepted
Secrets: provider keys в БД шифруются Fernet (AES-GCM класса), master key — только env
`MASTER_ENCRYPTION_KEY`. Наружу — маска `abcd...WXYZ`. Логи redact-ят токены/ключи/Authorization.

## ADR-009 | 2026-09-18 | accepted (уточнить после R-TG)
Streaming в Telegram: Rich Message Draft (Bot API 10.3) -> fallback sendMessageDraft ->
fallback throttled edit. `can_stop=true`, обработка `stopped_message_generation`,
throttle обновлений (мс/символы), partial answer сохраняется при Stop.

## ADR-010 | 2026-09-18 | accepted
Model capabilities централизованы в ModelRegistry + runtime capability probe
(scripts/smoke_providers.py), результат кешируется (provider_health). Mini App
показывает только реально поддерживаемые thinking-варианты. До probe — безопасный
fallback (provider default). Противоречивые доки Alibaba не угадываем.

## ADR-011 | 2026-09-18 | accepted
Memory: abstraction MemoryRetriever; v1 — PostgreSQL FTS; pgvector/локальные
embeddings — optional позже. Дедуп/update перед вставкой; retrieval учитывает
relevance/importance/recency; строгая изоляция по user_id.

## ADR-012 | 2026-09-18 | accepted
Web content — untrusted data (prompt-injection safe). open_url: только http/https,
DNS resolve + SSRF checks после каждого redirect, блок private/loopback/link-local/
metadata, max bytes, truncation по token budget.
