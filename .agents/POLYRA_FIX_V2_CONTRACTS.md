# POLYRA FIX V2 — интеграционные контракты (согласовано lead, 2026-09-24)

Источник: SUBAGENT_PLAN.md «нулевой шаг». Кратко, без многодневного дизайна.

## 1. Stream contract (терминальный)
- Каждый provider stream завершается ровно одним терминальным событием `Done`,
  и `Usage` (если провайдер его даёт) приходит ДО Done, никогда после.
  (Alibaba: usage-чанк с `choices: []` приходит после finish_reason — парсер
  буферизует и эмитит Usage до Done.)
- Отсутствие Done к концу потока = `unexpected EOF` → ошибка, а не успех.
- `GeminiProjectPool.stream_with_failover`: report_success/reconcile вызываются
  до завершения генератора (после последнего yield), закрытие transport — всегда
  (finally), CancelledError — без report_error, но с закрытием.

## 2. Usage ledger
- `generation_runs` — агрегат запуска: сумма input/output/reasoning по ВСЕМ
  LLM-вызовам (несколько раундов tools, несколько попыток пула — суммируем
  только per-call usage, не cumulative chunks одного вызова).
- `generation_runs.gemini_project_id` — последний использованный проект;
  attempts — в metadata (список имён проектов), добавить колонку
  `attempts JSONB` (миграция 0009, владелец G).
- Cancelled/failed runs тоже сохраняют известные usage; unknown ≠ 0 (NULL).
- `generation_runs.chat_id` → NULLable + ON DELETE SET NULL (миграция 0009, G):
  удаление чата НЕ стирает usage-ledger (A08).

## 3. Settings/effective
- Приоритет: chat override → user default → DB system_settings → env bootstrap.
- Runtime читает DB system_settings на КАЖДЫЙ запрос (короткая сессия, без кеша
  на старте; таблица маленькая). Admin → System меняет поведение со следующего
  LLMRequest (A13).
- unknown/disabled model в сохранённых настройках → явный отказ (не silent
  fallback на default) (A25).
- Пустой allowlist `[]` = запрет всех моделей; NULL = unrestricted. Миграция
  0009 фиксирует существующие пустые результаты (G).
- Owner identity: только `telegram_user_id == settings.owner_telegram_id`
  (795063564). Флаг is_owner в БД не даёт прав; миграция 0009 очищает чужие
  флаги (A04).

## 4. Tools policy
- Effective tools вычисляются один раз на запрос: registry ∩ permissions
  (grant) ∩ chat-level web/memory off. Тот же набор — и в LLMRequest.tools,
  и в ToolRunner (per-call enforcement). ToolCall на инструмент вне набора →
  denied без side effects (A12).
- Tool loop: общий max iterations (8), max calls/round (4), общий deadline
  (настраиваемый, default 240s), cancel проверяется ПЕРЕД каждым вызовом (A39/A11).
- Assistant turn в истории: текст раунда + tool_call parts (+ provider_meta с
  thought signatures для Gemini — не ломать) (A39).

## 5. Quota/pool (Gemini)
- check+reserve — одна БД-транзакция (SELECT ... FOR UPDATE или условный UPDATE)
  (A09, владелец C совместно с G-репозиторием; схему меняет только G).
- reconcile — по reservation (minute_ts/day захвачены при reserve), идемпотентно.
- Cooldown: project+model scope для 429; PERMISSION_DENIED → disable ключа;
  401 → disable; 5xx/network → bounded retry того же проекта (1 раз) → next;
  400/safety → без ротации (A10).

## 6. Context
- Summary покрывает диапазон ДО covered_until_message_id; builder берёт весь
  непокрытый сегмент в рамках бюджета, а не только «последние N» (A15).
- Бюджет считается на полный payload (system+memories+summary+recent+current+
  images+tools+tool results+output reserve+margin) перед каждым LLM call (A16).
- Пустая/невалидная summary не продвигает covered_until; compaction
  сериализуется per-chat (lock) и не откатывает более новую границу (A17).

## 7. Фото в истории
- MessagePart image хранит telegram_file_id (+file_unique_id, size, mime).
  bytes в БД не храним; rehydration через bot.get_file/download по требованию
  с кешем в рамках запроса и bounded размером (A18).

## 8. Telegram delivery
- Plain-text tiers: parse_mode=None явно (бот по умолчанию HTML) (A20).
- Tail только у временного draft; final/cancelled partial — полная разбивка
  по лимитам без потерь; tier3 final = edit (not-modified = успех) (A21).
- Throttle: last_success/next_attempt_at + retry_after + backoff; первый
  неудачный flush не глушит поток (A22).
- Stop: supervisor отменяет task (task.cancel) + cancellation event; провайдеры
  реагируют на отмену между чанками; tool runner — перед каждым вызовом (A11).

## 9. Mini App
- Все публичные UUID — string в TS-типах; никаких Number(id) (A01).
- /admin → hash route /#/admin; menu button ставится при startup глобально (A02).
- Списки: пагинация; archived фильтр отдельно (A24).
- Raw overrides и effective settings возвращаются раздельно (A25).
- Sources: показываются, когда был реальный поиск (см. ADR-017-конфликт:
  владелец просил выключить — оставляем SHOW_SOURCES flag, но default ВКЛ по V2 A32;
  отмечено в FIX_REPORT_V2).

## 10. Secrets/keys
- Gemini key identity: стабильный HMAC-fingerprint полного ключа (не last4);
  key_hint остаётся маской для UI (A29).
- disabled provider credential — абсолютный запрет; env fallback только когда
  записи нет вовсе; смена конфига закрывает cached client (A14/A30).
- HTTP-клиенты — singleton/lifecycle-owned (созданы при старте, закрыты при
  shutdown), никаких клиентов на каждый вызов (A30).
