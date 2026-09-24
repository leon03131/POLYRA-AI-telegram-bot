# План реальных субагентов для OpenCode

Это организация работы N01/N02, а не утверждение, что субагенты уже запущены.
Максимум четыре активных дочерних агента. Специализаций восемь; запускать их волнами
или через очередь готовых независимых задач. Все берут текущую Kimi-модель родителя,
если в фактической конфигурации не задано иное; override проверить, не угадывать.

| Профиль | Ответственность | Основные пункты |
|---|---|---|
| `polyra-telegram` | A — Telegram / streaming / Stop | A02, A11, A18, A20, A21, A22 |
| `polyra-miniapp` | B — Mini App / UX / типы | A01, A24, A25, A26, A28, A40, N04 |
| `polyra-gemini` | C — Gemini / pool / квоты | A06, A09, A10, A11, A23, A27, A30, A39 |
| `polyra-alibaba` | D — Alibaba / registry / DeepSeek V4 Pro | A06, A11, A23, A27, A30, A39, N03, N04 |
| `polyra-context` | E — context / memory / compaction | A15, A16, A17, A18, A40 |
| `polyra-search-security` | F — tools / search / SSRF | A12, A19, A30, A31, A32, A33, A39 |
| `polyra-db-api` | G — DB / migrations / API / access | A01, A03, A04, A05, A08, A09, A12, A13, A14, A24, A25, A26, A28, A29, A34, A35, N04 |
| `polyra-qa-release` | H — regression / security review / release | A36, A37, A38, A40, N01, N02, N03, N04 |

## Главный агент — интегратор

Его начальный exclusive scope:
`app/services/generation.py`, `app/services/llm_factory.py`, `app/llm/base.py`,
`app/llm/events.py`, `app/llm/router.py`, `app/config.py`, `app/main.py`,
`app/api/app.py`, `app/observability/**`, `tests/unit/test_generation.py`,
общие fixtures, `pyproject.toml`, lockfiles, CI/deployment и root docs.

Детальные остальные scopes находятся в соответствующих профилях.
Shared файлы можно передать одному субагенту отдельной записью dispatch.
После передачи главный агент сам их НЕ правит до возврата владения.
Владелец DB-схемы — G; архитектуру/контракты утверждает главный агент.
Единственный writer registry/probe — D; C/E/G/B передают изменения ему.
Shared API types принадлежат B после согласования контракта с G.
Неорганизованные параллельные правки всех восьми исполнителей запрещены.

## Нулевой шаг: текущий checkout и базовые контракты

Проверить git status/diff и не потерять изменения владельца. Сопоставить текущую
версию с историческим аудитом без полного повторного аудита каждого уже исправленного
файла. Записать commit/dirty status, baseline tests и blockers.
Список findings не принимать слепо: confirmed / already fixed / needs verification.

Зафиксировать `.agents/POLYRA_FIX_V2_CONTRACTS.md`:
- терминальный stream contract: final usage, один terminal event, cancel/finalizer;
- admission/usage ledger/reservation и идентификаторы окон;
- raw/effective settings, model ID/UUID, enabled/allowlist;
- capability evidence отдельно от transient health;
- context coverage/current message/tool budget;
- image reference, tool policy/cancel и safe Telegram delivery.

Не превращать это в многодневное проектирование. Кратко согласовать API, затем
дать независимым исполнителям работать.

## Пример расписания

**Волна исследования:** B, D, E, G параллельно читают свои участки и только свои
пункты аудита; каждый пишет отдельный отчёт. Общие production files пока не меняют.
Главный агент анализирует orchestration и согласует ответы. При наличии готовых
контрактов запуск implementation не задерживать ожиданием несвязанного исследования.

**Первая coding-волна:** B (UI), D (Alibaba/Pro/registry), E (context), F (search/tools).
Scopes непересекающиеся. Backend-зависимые UI tests могут сначала использовать
согласованные fixtures, но это не замена итоговой интеграции.

**Вторая coding-волна:** A (Telegram), C (Gemini), G (DB/API), H (новые integration tests).
D/E/F передают запросы на schema/wiring главному агенту/G. H использует отдельную
test database/схему, не мутирует общий production instance.

**Интеграция и приёмка:** главный агент собирает совместную реализацию; H проверяет
её независимо. B/D/C можно запускать для узких регрессий, если их файлы освобождены
и не превышен лимит четырёх.

Это пример dependency-aware расписания, а не требование простаивать до завершения
самой медленной несвязанной задачи. Запускать следующий готовый scope при освобождении
слота. Сократить число активных запросов при реальных лимитах OpenCode-провайдера,
не устраняя саму практику делегирования.

## Минимальная карточка задачи

Передавать каждому: audit IDs + N01–N04 invariants + точные writable paths +
read-only dependencies + acceptance tests + общий deadline/budget live probes +
формат отчёта. Не пересылать всем подряд весь static_analysis.json и весь старый prompt.

Каждый агент пишет только в
`.agents/reports/fix-v2/<реальное-имя-агента>/<task-id>.md`:
scope; изменённые файлы; ключевые решения; test commands/exit codes;
известные blockers; что требуется от интегратора; что НЕ проверено.

При общей рабочей папке нельзя одновременно переключать git branch, cherry-pick,
commit/reset/clean или мигрировать одну БД. Git-операциями общей папки управляет lead.
Отдельные worktrees допустимы, только если назначены и реально изолированы; ни task,
ни child session сами по себе их не гарантируют.

## Проверка завершения

Lead сверяет changed paths с выделенным scope, просматривает diff, принимает/отклоняет
результат, интегрирует, затем запускает общие tests. Слово «готово» в отчёте субагента
без evidence не закрывает пункт. Dispatch log содержит реальные tool/session references
при наличии; если среда их не выдаёт, написать это, а не генерировать похожие UUID.
