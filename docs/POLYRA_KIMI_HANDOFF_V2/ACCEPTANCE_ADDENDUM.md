# Дополнительная приёмка версии 2

Дополняет все A01–A40 и общие критерии в `KIMI_FIX_PROMPT.txt`.
Это план будущих проверок. Ни один пункт здесь не помечен уже пройденным.

## N01 — доказательство делегирования

Фактические child sessions / task calls указаны в `.agents/POLYRA_FIX_V2_DISPATCH.md`.
Названия ролей в тексте без реального запуска не считаются выполнением.
Есть параллельная работа независимых задач, когда среда её поддерживает; максимум
четыре активных child agents, без рекурсивного размножения. При реальной блокировке
статус blocked и причина, а не выдуманные session IDs или результаты.

Есть file ownership, общий integration contract, отчёты исполнителей и review их diff.
В shared worktree отсутствуют параллельные правки одного файла и конкурентные git
checkout/commit/reset. Отдельные worktrees — только по явному назначению координатора;
создание child session само по себе НЕ гарантирует отдельный worktree.

## N02 — Desktop/runtime

Указаны реально обнаруженные версия/ветка (или честное unknown), tool name и агент,
который делегирует. Применён совместимый локальный формат без удаления пользовательских
настроек, смены provider/key/model, повышения всех разрешений или установки чужого runtime.
Профили действительно обнаружены, либо используется штатный subagent с тем же scope.
Сам факт копирования восьми Markdown-файлов ещё не доказывает запуск.

## N03 — матрица регрессий всех моделей

`MODEL_REGRESSION_MATRIX.md` — шаблон; перед сдачей заполнить на текущем commit.
Проверять минимум 10 baseline ID с новой Pro; внутреннюю модель — отдельно.
Существующие модели/настройки/разрешения не удалены «ради исправления».
Любые более новые пользовательские модели из текущего checkout также сохранены.

Обязательные offline сценарии:
- нормальный text stream и явное завершение;
- reasoning не появляется в Telegram/UI/обычных message bodies;
- usage trailer после finish не теряется, tool-round usage суммируется;
- 400 не превращается в перебор модели/ключей; auth и temporary errors различаются;
- 429, Retry-After, 5xx, timeout, оборванный SSE, malformed frame и fragmented tools;
- partial -> ошибка без дублирования ответа/side effects;
- cancel во время ожидания сети и tools; finalizers завершаются корректно;
- transient probe failure не выключает модель и не стирает positive capability;
- parameter accepted не выдаётся за самостоятельную семантику режима;
- admin-disabled credential не оживает через env fallback;
- точный model ID не меняется при retry, rotation и ошибке;
- inherited/effective model/thinking/prompt доходят до фактического LLMRequest.

Различать capability states supported/unsupported/unknown и состояние transport health.
Пропуск, no-key, limited-budget и timeout не эквивалентны unsupported.
Старый успешный probe хранится с timestamp/TTL и stale-marker, не как вечная гарантия.

Небольшой повторный opt-in smoke batch (число повторов и общий бюджет явно ограничены)
нужен для наблюдения нестабильности; не запускать тысячи запросов, не обещать SLA
по трём успешным ответам, не тратить quota всех 30 Gemini-проектов автоматически.

## N04 — DeepSeek V4 Pro

Регистрация:
- exact ID `deepseek-v4-pro`, provider alibaba, internal_only false;
- `deepseek-v4.1-flash` остаётся отдельной моделью;
- Pro не получает чужой image flag;
- исходный Alibaba endpoint и direct transport сохранены;
- Pro не меняет выбранную пользователем default-модель.

Payload/UI:
- DEFAULT не эквивалентен OFF;
- OFF/HIGH/MAX имеют ожидаемый payload и честный статус подтверждения;
- LOW не представлен как новый уровень на основании accepted alias;
- специфичные параметры Qwen/GLM/Flash не добавляются по ошибке;
- лимит output не равен автоматически maximum provider ceiling;
- поле output budget реально поддерживается выбранным adapter/API.

Вертикальные тесты:
1. Owner видит Pro и может выбрать/сохранить её.
2. User с allowlist без Pro получает отказ до внешнего запроса.
3. User с allowlist [Pro] может использовать Pro, но не Flash.
4. [] запрещает всё; explicit unrestricted включает новые публичные enabled модели
   согласно общей политике; добавление Pro не расширяет существующий explicit allowlist.
5. Reload/API/effective settings сохраняют exact ID и допустимый thinking.
6. Pro final stream и usage сохраняются раздельно от Flash; несколько tool rounds
   корректно сериализованы и учитываются.
7. Фото на Pro вызывает понятную ошибку; нет потери файла и cross-model fallback.
8. Ошибка/Stop не создаёт completed run и не выдаёт hidden reasoning.

## Что сдавать

`FIX_REPORT_V2.md`:
- таблица A01–A40 и N01–N04;
- status: fixed / already-fixed-and-verified / open / blocked / optional-deferred;
- изменённые файлы, commit или идентификатор diff, test name, command, exit code,
  evidence artifact, ограничения;
- полные итоговые логи отдельно от старых `audit_original/evidence/`;
- реальный список делегирований и результаты integration review;
- отдельные offline и live статусы каждой модели и сценария.

Skipped не является passed. `fixed` по коду не тождественно `live-verified`.
A40 можно оставить optional-deferred с обоснованием; P1/P2 и N04 так не закрывать.
Если runtime/сеть/ключи/БД недоступны, выполнить доступную реализацию и tests,
перечислить blockers и команды для непроверенного, не заявляя полного завершения.
