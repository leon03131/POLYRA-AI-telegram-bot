# KNOWN ISSUES / риски на трекере

## Deployment (2026-09-24, VPS SberCloud 176.108.245.225; старый 72.56.252.97 мёртв)

- **Хостер фильтрует часть api.telegram.org**: 149.154.166.110/175.53/161.20 — DROP,
  149.154.167.220 — работает. Решение: `extra_hosts` pin в docker-compose.yml (app service).
  Если Telegram сменит/заблокирует этот IP — убрать pin или настроить TELEGRAM_PROXY (WARP).
  WARP-вариант заготовлен но НЕ активирован: Cloudflare /reg отвечает 429 с IP хостера
  (wgcf + wireproxy лежат на сервере в /usr/local/bin, профиль не создан).
- Остальные сети с VPS (google/github/netlify/dashscope) — работают напрямую.

Обновлено: 2026-09-18 (после M11-M12 + live probe).

**Live probe 2026-09-18 (реальные ключи владельца):**
- Alibaba: 27/27 OK — все 5 моделей (text/stream/FC/image), все thinking-уровни приняты
  (включая OFF у deepseek-v4.1-flash и kimi-k3), Kimi Dynamic Tool Loading РАБОТАЕТ на
  MS endpoint (200 + tool_calls). probe_required снят с обеих моделей.
- Gemini через proxy владельца: путь работает (gemini-3.6-flash ответила, usage корректен);
  первые попытки дали 503 «high demand»/504 — транзиентно, pool-фейловер это покрывает.
- qwen3.8-flash image: первичный fail был артефактом теста (1x1 px < минимума 10px у API).

1. ~~Docker недоступен в dev-среде, миграции не проверялись~~ **Закрыто
   2026-09-24**: миграции 0001–0009 применены на проде (VPS SberCloud,
   `alembic upgrade head` в CMD контейнера, живой PostgreSQL 16); docker
   build/up на VPS прошёл, healthcheck зелёный.
2. **ai.google.dev недоступен из dev-сети** — факты по Gemini восстановлены по Wayback
   Machine (13–17.09.2026) и Vertex-зеркалу. Runtime-путь подтверждён live probe
   (см. выше); doc-факты перепроверить с рабочей сети при случае.
3. **Serper docs недоступны** (404/transport) — формат запроса de-facto стандарт,
   верифицировать после регистрации ключа (первый реальный вызов).
4. ~~Runtime capability probes не выполнялись~~ **Закрыто**: live probe 2026-09-18
   (27/27 OK, см. выше) + `--strict` прогон на VPS 2026-09-24 (39 OK / 0 fail /
   3 skipped) — включая Kimi thinking и Gemini custom base_url.
5. **Windows/cp1251**: держать конфиг-файлы ASCII (alembic.ini уже переведён);
   при необходимости запускать с `PYTHONUTF8=1`.
6. **Rate limits Telegram draft updates не документированы** — throttle ~1 об/сек
   + обработка 429 (retry_after), подобрано эмпирически (M5).
7. **aiogram docs хостятся под /dev-3.x/** — ключевые методы сверены с git-тегом v3.31.0.
8. ~~Kimi K3 Max Output=1M подозрительно~~ **Закрыто 2026-09-24**: подтверждено
   владельцем, `max_output=1_048_576` в `app/llm/capabilities.py`. max_tokens
   по-прежнему не передаём без необходимости.
9. **SSRF: DNS rebinding / TOCTOU — закрыт 2026-09-24** (A19): pinned-connect в
   `app/search/fetcher.py` (`PinnedHTTPTransport`: TCP/TLS к проверенному IP,
   Host/SNI по реальному хосту, TLS verify включён, каждый redirect проверяется
   заново). Остаётся как известная заметка к первичному риску (security review
   2026-09-18, medium): опциональное усиление — egress-прокси. Mitigation на
   месте: per-redirect проверки, 2 МБ cap, таймауты.
10. **Owner id зашит в default config** (795063564) — per ТЗ; смена owner в проде требует
    env OWNER_TELEGRAM_ID + ручного снятия is_owner у прежнего (is_owner не отзывается
    автоматически).

## FIX V2 (2026-09-24) — открытые остатки

- PostgreSQL barrier/race integration (A05/A09 формально на живой БД с двумя
  соединениями) — не прогнан в этой среде; код-инварианты покрыты unit/контрактами.
- Telegram draft rate limit остаётся эмпирическим (throttle 1/с + retry_after).
- A40 остатки (optional-deferred): полный lockfile, a11y-тесты Mini App,
  backup retention policy.
- Конфликт по источникам: владелец просил выключить блок «Источники» (19.09),
  V2 A32 требует их при реальном поиске — выбран V2 (default SHOW_SOURCES=1);
  отключить: SHOW_SOURCES=0 в .env.
