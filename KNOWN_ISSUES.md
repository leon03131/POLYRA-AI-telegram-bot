# KNOWN ISSUES / риски на трекере

Обновлено: 2026-09-18 (после M2).

1. **Docker недоступен в dev-среде** — `alembic upgrade head` против живой PostgreSQL 16
   не выполнялся; миграции проверены только на загрузку (`alembic heads/history`).
   Проверить при деплое или в CI с postgres service.
2. **ai.google.dev недоступен из dev-сети** — факты по Gemini восстановлены по Wayback
   Machine (13–17.09.2026) и Vertex-зеркалу. Перепроверить с рабочей сети перед релизом.
3. **Serper docs недоступны** (404/transport) — формат запроса de-facto стандарт,
   верифицировать после регистрации ключа (первый реальный вызов).
4. **Runtime capability probes не выполнялись** (нет API-ключей в среде):
   `scripts/smoke_providers.py` (M3+) — особенно Kimi thinking и Gemini custom base_url.
5. **Windows/cp1251**: держать конфиг-файлы ASCII (alembic.ini уже переведён);
   при необходимости запускать с `PYTHONUTF8=1`.
6. **Rate limits Telegram draft updates не документированы** — throttle ~1 об/сек
   + обработка 429 (retry_after), подобрать эмпирически (M5).
7. **aiogram docs хостятся под /dev-3.x/** — ключевые методы сверены с git-тегом v3.31.0.
8. **Kimi K3 Max Output=1M на странице модели** — подозрительно; max_tokens не передавать
   без необходимости, уточнить probe.
