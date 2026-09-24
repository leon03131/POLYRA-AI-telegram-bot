# FIX_REPORT_V2 — шаблон, не отчёт об уже выполненных исправлениях

Рабочий commit/diff: заполнить. Dirty status: заполнить.
OpenCode runtime/version и реальные tools: заполнить.
Период работы: заполнить. Использованные субагенты/dispatch: заполнить.

| ID | Предмет | Статус | Файлы / commit | Тест / команда / exit code | Evidence / ограничения |
|---|---|---|---|---|---|
| A01 | Настройки существующего чата ломаются из-за преобразования UUID в число | not_started | — | — | — |
| A02 | Команда /admin ведёт на 404; menu button настраивается не при startup | not_started | — | — | — |
| A03 | Пустой список разрешённых моделей превращается в доступ ко всем моделям | not_started | — | — | — |
| A04 | Admin authorization доверяет флагу is_owner помимо numeric Telegram ID | not_started | — | — | — |
| A05 | Нет атомарного захвата чата и пользовательских лимитов перед генерацией | not_started | — | — | — |
| A06 | Done обрывает поток до usage и финализации Gemini-пула | not_started | — | — | — |
| A07 | Даже после исправления Done расход разных tool-раундов не суммируется | not_started | — | — | — |
| A08 | Удаление чата обнуляет суточный учёт запросов пользователя | not_started | — | — | — |
| A09 | Gemini quota check/reserve не атомарны, reconcile попадает в другое окно | not_started | — | — | — |
| A10 | Cooldown и retry Gemini не соответствуют требуемой области действия | not_started | — | — | — |
| A11 | Stop не гарантирует немедленного прерывания HTTP stream и tools | not_started | — | — | — |
| A12 | Memory Off/Web Off не являются полной серверной политикой tools | not_started | — | — | — |
| A13 | Настройки Admin → System сохраняются, но генерация их не читает | not_started | — | — | — |
| A14 | Отключённый Alibaba credential снова активируется через env fallback | not_started | — | — | — |
| A15 | История теряется из контекста до гарантированной суммаризации | not_started | — | — | — |
| A16 | TokenBudget не ограничивает фактически отправляемый запрос | not_started | — | — | — |
| A17 | Пустой JSON считается успешной summary; фоновые compaction могут перезаписываться | not_started | — | — | — |
| A18 | Фото не сохраняет Telegram file_id и исчезает из последующей истории LLM | not_started | — | — | — |
| A19 | SSRF-защита оставляет DNS rebinding / resolve-then-connect окно | not_started | — | — | — |
| A20 | Fallback Telegram отправляет raw LLM text с глобальным HTML parse_mode | not_started | — | — | — |
| A21 | Длинный rich answer/partial обрезается; final edit может дублировать ответ | not_started | — | — | — |
| A22 | Throttle не обрабатывает retry_after и ломается после первой ошибки flush | not_started | — | — | — |
| A23 | Незавершённый/повреждённый SSE может быть принят за успешный ответ | not_started | — | — | — |
| A24 | Архив и длинные списки чатов недоступны через Mini App | not_started | — | — | — |
| A25 | Effective settings и модельная валидация расходятся между UI, API и runtime | not_started | — | — | — |
| A26 | Admin не может снять числовой лимит; обещанная остановка при revoke не реализована | not_started | — | — | — |
| A27 | Capability probe неполон и не управляет runtime capabilities | not_started | — | — | — |
| A28 | Полная административная панель из ТЗ не реализована | not_started | — | — | — |
| A29 | Импорт Gemini keys ошибочно считает последние четыре символа идентификатором ключа | not_started | — | — | — |
| A30 | HTTP clients поиска и провайдеров не имеют управляемого lifecycle | not_started | — | — | — |
| A31 | JinaReader реализован, но не подключён к open_url в рабочем пути | not_started | — | — | — |
| A32 | Источники и AI Overview не доведены до требуемого end-to-end контракта | not_started | — | — | — |
| A33 | Некорректная структура ответа search backend обрывает fallback | not_started | — | — | — |
| A34 | После рестарта не восстанавливаются stale runs; pending Telegram updates удаляются | not_started | — | — | — |
| A35 | Наблюдаемость и аудит админских операций неполны | not_started | — | — | — |
| A36 | Тесты не доказывают важные инварианты и местами закрепляют ошибки ТЗ | not_started | — | — | — |
| A37 | Deployment требует проверки сигналов, readiness и безопасного сетевого профиля | not_started | — | — | — |
| A38 | Документация и отметки DONE противоречат коду и собственным probe-отчётам | not_started | — | — | — |
| A39 | Tool-loop теряет часть assistant turn и недостаточно ограничен по общей работе | not_started | — | — | — |
| A40 | Рекомендуемое усиление памяти, ограничений входа и воспроизводимости | not_started | — | — | — |
| N01 | Реальное обязательное делегирование: до 4 активных субагентов | not_started | — | — | — |
| N02 | Совместимость с текущим OpenCode Desktop и сохранение настроек | not_started | — | — | — |
| N03 | Сохранение моделей и диагностика нестабильности | not_started | — | — | — |
| N04 | Сквозная интеграция deepseek-v4-pro через Alibaba | not_started | — | — | — |

Финальные статусы: fixed / already-fixed-and-verified / open / blocked / optional-deferred.
`not_started` оставлен только как начальный маркер шаблона. A40 может быть отложен;
required пункты не закрывать формулировкой optional.

## Общие проверки

pytest; Ruff; mypy/pyright; frontend typecheck/build/E2E; PostgreSQL fresh/upgrade/race;
Docker config/build/start/shutdown; backup/restore. Для каждой: команда, версия среды,
exit code, итог, путь к полному логу. Не копировать старые логи как новую проверку.

## Модели

Приложить MODEL_REGRESSION_MATRIX.md; отдельно offline vs live, дата/endpoint/mode,
pass/fail/skipped/blocked, причины ошибок, число и бюджет проб. Без secrets/reasoning.

## Делегирование

Реальные scopes, child references (если доступны), время, changed files, integration review.
Возможные отклонения от плана и ограничения оболочки.

## Непроверенное и незавершённое

Перечислить явно. Код реализован != live проверен. Неполные проверки != общий pass.
