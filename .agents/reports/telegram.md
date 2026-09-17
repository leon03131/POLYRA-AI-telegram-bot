# R-TG: Telegram Bot API streaming (drafts / Rich Messages) + покрытие aiogram

Дата проверки: 2026-09-18
Субагент: R-TG (только исследование; production code не изменялся)

## Источники

| URL | Статус | Что извлечено |
|---|---|---|
| https://core.telegram.org/bots/api | получено (полный текст, ~530 КБ) | Сигнатуры sendMessageDraft, sendRichMessageDraft, sendRichMessage, editMessageText; типы MessageGenerationStopped, RichMessage, InputRichMessage, InputRichMessageMedia, RichText*, RichBlock*/InputRichBlock*; разделы Rich Message Formatting Options / Limits / Rich Markdown / Rich HTML; Update.stopped_message_generation |
| https://core.telegram.org/bots/api-changelog | получено | Даты и состав Bot API 9.3 → 10.3 |
| https://core.telegram.org/bots/features | получено | Разделы «Streaming Replies», «Rich Messages», «Ephemeral Messages» |
| https://docs.aiogram.dev/en/dev-3.x/api/methods/index.html | получено (страница помечена «aiogram 3.31.0 documentation») | В индексе есть sendMessageDraft, sendRichMessage, sendRichMessageDraft |
| https://docs.aiogram.dev/en/dev-3.x/api/types/index.html | получено | В индексе есть MessageGenerationStopped, RichMessage, RichMessageButton, все RichText*/RichBlock*/InputRichBlock*/InputRichMessage* |
| https://pypi.org/simple/aiogram/ | получено | На PyPI последняя версия — 3.31.0 (whl + sdist); 3.31.1 нет |
| https://github.com/aiogram/aiogram/releases | получено | v3.31.0 «Telegram Bot API 10.3» от 2026-08-25 (PR #1888 «Added full support for the Bot API 10.3»); v3.30.0 = Bot API 10.2 (2026-07-17); v3.29.0 = Bot API 10.1 (2026-06-14) |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/methods/__init__.py | получено | Экспортируются SendMessageDraft, SendRichMessage, SendRichMessageDraft |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/methods/send_message_draft.py | получено | Поля: chat_id, draft_id, message_thread_id, text, parse_mode, entities, can_stop, keep_on_stop |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/methods/send_rich_message_draft.py | получено | Поля: chat_id, draft_id, rich_message, message_thread_id, can_stop, keep_on_stop |
| https://raw.githubusercontent.com/aiogram/aiogram/v3.31.0/aiogram/methods/send_rich_message_draft.py | получено | Идентично dev-3.x → фича входит в релиз 3.31.0, не только в dev-ветку |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/types/update.py | получено | Update.stopped_message_generation: MessageGenerationStopped \| None; event_type → "stopped_message_generation" |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/types/input_rich_message.py | получено | Поля InputRichMessage: html / markdown / blocks / media / is_rtl / skip_entity_detection |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/dispatcher/router.py | получено | В Router есть TelegramEventObserver `stopped_message_generation` + запись в observers |
| https://raw.githubusercontent.com/aiogram/aiogram/dev-3.x/aiogram/client/bot.py | получено (полный, ~340 КБ) | Bot.send_message_draft, Bot.send_rich_message, Bot.send_rich_message_draft, Bot.edit_message_text(rich_message=...), Bot.edit_ephemeral_message_text(rich_message=...) |
| https://raw.githubusercontent.com/aiogram/aiogram/v3.31.0/pyproject.toml | получено | Версия из aiogram/__meta__.py; зависимости (pydantic >=2.4.1,<2.14 и т.д.) |

## Версии (подтверждено changelog + релизами aiogram)

- Bot API 9.3 — 2025-12-31: добавлен **sendMessageDraft** (стриминг черновиков; изначально ограниченно).
- Bot API 9.5 — 2026-03-01: sendMessageDraft разрешён **всем ботам**.
- Bot API 10.0 — 2026-05-08: в sendMessageDraft разрешён **пустой text** (плейсхолдер «Thinking…»).
- Bot API 10.1 — 2026-06-11: **Rich Messages**: RichText*, RichBlock*, RichMessage, InputRichMessage (тогда только html/markdown), InputRichMessageContent, **sendRichMessage**, **sendRichMessageDraft**, параметр rich_message в editMessageText.
- Bot API 10.2 — 2026-07-14: InputRichMessageMedia + поле media в InputRichMessage; все **InputRichBlock\*** + поле blocks в InputRichMessage; Ephemeral Messages; Communities.
- Bot API 10.3 — 2026-08-24: **can_stop и keep_on_stop** в sendMessageDraft и sendRichMessageDraft; **MessageGenerationStopped + Update.stopped_message_generation**; кнопки в rich messages (RichMessageButton, RichTextButton, RichBlockButtons/InputRichBlockButtons); RichBlockExpandableBlockQuotation; RichBlockDocument; ephemeral_message_parameters в sendRichMessage; DisabledButton.
- aiogram: **3.31.0 (2026-08-25) = полная поддержка Bot API 10.3**; 3.30.0 = 10.2; 3.29.0 = 10.1. На PyPI 3.31.0 — последняя. В проекте (PLAN.md, M5) запланирован aiogram 3.31.x и цепочка «Rich Draft -> sendMessageDraft -> throttled edit; stopped_message_generation» — исследование это подтверждает как реализуемое.

## sendMessageDraft / sendRichMessageDraft — точные сигнатуры и ограничения

### sendMessageDraft (Bot API 9.3+, can_stop/keep_on_stop с 10.3)

> «stream a partial message to a user while the message is being generated… draft is ephemeral and acts as a temporary **30-second preview** — once the output is finalized, you **must** call sendMessage with the complete message to persist it». Возвращает `True`.

| Параметр | Тип | Обяз. | Описание |
|---|---|---|---|
| chat_id | Integer | Да | ID **приватного** чата (только private; @username и группы не поддерживаются) |
| message_thread_id | Integer | нет | Тред (для приватных чатов с включёнными топиками) |
| draft_id | Integer | Да | ID черновика, **не ноль**; обновления с тем же draft_id анимируются, иначе черновик заменяется без анимации |
| text | String | нет | 0–4096 символов после парсинга entities; **пустой текст = плейсхолдер «Thinking…»** |
| parse_mode | String | нет | Обычные formatting options (MarkdownV2/HTML) |
| entities | MessageEntity[] | нет | Вместо parse_mode |
| can_stop | Boolean | нет | Показать пользователю кнопку Stop; при нажатии бот получает Update stopped_message_generation |
| keep_on_stop | Boolean | нет | Оставить черновик в чате после Stop; он всё равно исчезнет «через короткое время» или когда бот отправит сообщение; для сохранения — отправить как новое сообщение |

### sendRichMessageDraft (Bot API 10.1+, can_stop/keep_on_stop с 10.3)

Тот же контракт (эфемерный 30-секундный превью; финализация через **sendRichMessage**). Возвращает `True`.

| Параметр | Тип | Обяз. | Описание |
|---|---|---|---|
| chat_id | Integer | Да | ID приватного чата |
| message_thread_id | Integer | нет | Тред |
| draft_id | Integer | Да | Как выше (не ноль; анимация при том же id) |
| rich_message | InputRichMessage | Да | Частичное сообщение; **прямая загрузка новых файлов и загрузка по URL не поддерживаются** |
| can_stop | Boolean | нет | Кнопка Stop → update stopped_message_generation |
| keep_on_stop | Boolean | нет | Как выше |

### sendRichMessage (финализация; Bot API 10.1+)

chat_id (Integer **или String @username** бота/супергруппы/канала), rich_message (InputRichMessage, обяз.), business_connection_id, message_thread_id, direct_messages_topic_id, ephemeral_message_parameters (10.3), disable_notification, protect_content, allow_paid_broadcast, message_effect_id, suggested_post_parameters, reply_parameters, reply_markup. Возвращает Message. Бот должен иметь право отправлять медиа, если в сообщении есть медиа-блоки.

### Ограничения

- **Видимость драфтов: только приватные чаты** (подтверждено типом chat_id=Integer «target private chat» и разделом «Streaming Replies» на странице features).
- Драфт эфемерен: ~30 секунд превью; не сохраняется в истории; финальное сообщение надо отправить отдельно (sendMessage/sendRichMessage). Метода «удалить драфт» нет.
- Rich Message Limits: до **32768** UTF-8 символов текста (включая alt-текст custom emoji и исходники формул); до **500 блоков** (включая вложенные, list items, строки таблиц, цитаты, details); до **16 уровней** вложенности; до **50 медиа**; до **20 колонок** в таблице.
- sendMessageDraft text: 0–4096 символов.
- Отдельных rate limits для драфтов в документации **не задокументировано** (см. «Пробелы»).
- `<tg-thinking>` / InputRichBlockThinking — **только в sendRichMessageDraft** (в персистентных сообщениях недопустим, в получаемых сообщениях не встречается).
- Авто-детект сущностей (URL, mention, hashtag и т.п.) в rich messages отключается через skip_entity_detection; клиенты показывают алерт перед открытием inline-ссылок.

## stopped_message_generation (Bot API 10.3)

- В `Update` добавлено поле `stopped_message_generation: MessageGenerationStopped` — «A user asked the bot to stop the generation of a message».
- Структура **MessageGenerationStopped**:
  - `chat: Chat` — чат, где идёт генерация;
  - `message_thread_id: Integer` (optional) — тред;
  - `draft_id: Integer` — **ID остановленного черновика**.
- Связь с запущенной генерацией: по паре `(chat.id, draft_id)` (+ `message_thread_id`, если используется). Поэтому draft_id должен быть уникален на активную генерацию (например, monotonically increasing id из БД/счётчика; переиспользование draft_id анимирует замену — это фича для обновлений одного драфта, а не для новых генераций).
- Кнопка Stop появляется только если в драфте передан `can_stop=True`.
- getUpdates/setWebhook: `stopped_message_generation` входит в дефолтный набор allowed_updates (по умолчанию исключены только chat_member, message_reaction, message_reaction_count). В aiogram при polling `resolve_used_update_types()` автоматически добавит тип, если зарегистрирован хендлер на `router.stopped_message_generation`; для webhook — добавить в allowed_updates явно.

## Rich Messages: минимальный набор для текстового стрима

**InputRichMessage** — ровно одно из полей: `html` | `markdown` | `blocks` (+ опционально `media`, `is_rtl`, `skip_entity_detection`).

Для LLM-стрима проще всего режим **markdown** (Rich Markdown ≈ GitHub Flavored Markdown + встроенные HTML-теги из Rich HTML): `**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, fenced code с языком, заголовки `#`–`######`, списки (в т.ч. task lists), `>` цитаты, таблицы, `---` divider, `$$...$$`/```math``` для LaTeX, футноты. Плюс тег `<tg-thinking>` (только в драфте).

Если строить блоками (InputRichBlockUnion), минимальный набор для markdown-подобного текста:

- `InputRichBlockParagraph` {type:"paragraph", text: RichText}
- `InputRichBlockSectionHeading` {type:"heading", text, size 1–6}
- `InputRichBlockPreformatted` {type:"pre", text, language?}
- `InputRichBlockList` {type:"list", items: InputRichBlockListItem[]} (item: {blocks, has_checkbox?, is_checked?, value?, type?})
- `InputRichBlockBlockQuotation` {type:"blockquote", blocks, credit?}
- `InputRichBlockDivider` {type:"divider"}
- `InputRichBlockThinking` {type:"thinking", text} — плейсхолдер «Thinking…», **только для драфтов**

**RichText** = String | Array<RichText> | типизированные объекты с полями {type, text, …}: RichTextBold («bold»), RichTextItalic, RichTextUnderline, RichTextStrikethrough, RichTextSpoiler, RichTextCode, RichTextMarked, RichTextSubscript/Superscript, RichTextUrl (+url), RichTextCustomEmoji, RichTextMathematicalExpression, RichTextDateTime, RichTextMention/Hashtag/Cashtag/BotCommand и др. (26 типов, 10.1 + RichTextButton в 10.3). Со стороны чтения: `Message.rich_message: RichMessage {blocks: RichBlock[], is_rtl?}`.

## Покрытие в aiogram 3.31

| Фича | Статус в aiogram | Где проверено |
|---|---|---|
| Bot API 10.3 целиком | есть, релиз **3.31.0** (2026-08-25, PR #1888) | GitHub releases, PyPI |
| Bot.send_message_draft(...) -> bool | **есть** (chat_id, draft_id, message_thread_id, text, parse_mode=Default("parse_mode"), entities, can_stop, keep_on_stop, request_timeout) | client/bot.py dev-3.x; methods/send_message_draft.py |
| Bot.send_rich_message_draft(...) -> bool | **есть** (chat_id, draft_id, rich_message, message_thread_id, can_stop, keep_on_stop) | client/bot.py; v3.31.0 tag |
| Bot.send_rich_message(...) -> Message | **есть** (включая ephemeral_message_parameters) | client/bot.py |
| Типы RichMessage / RichText* / RichBlock* | **есть** (полный набор, включая RichMessageButton, RichBlockThinking) | docs types index 3.31.0 |
| Типы InputRichMessage / InputRichMessageMedia / InputRichBlock* / InputRichMessageContent | **есть** (InputRichMessage: html/markdown/is_rtl/skip_entity_detection/blocks/media) | docs types index; types/input_rich_message.py |
| MessageGenerationStopped + Update.stopped_message_generation | **есть**; Update.event_type → "stopped_message_generation" | types/update.py dev-3.x |
| Хендлер события | **есть**: `router.stopped_message_generation` (TelegramEventObserver в Router) | dispatcher/router.py dev-3.x |
| editMessageText(rich_message=...) | **есть** (также edit_ephemeral_message_text) | client/bot.py |
| sendMessageDraft исторически | метод присутствует как минимум с 3.25–3.26 эпохи (Bot API 9.4/9.5); в 3.31.0 с can_stop/keep_on_stop | methods/__init__.py, releases |

Вывод: **raw-вызовы Bot API для drafts/rich в 3.31.0 не нужны** — всё покрыто нативно, включая событие остановки.

## Противоречия / пробелы документации

1. **Rate limits для драфтов не документированы.** Ни на странице API, ни в features, ни в FAQ (там только общие лимиты рассылки) нет минимального интервала обновления draft. Нужен клиентский throttle и обработка 429 (retry_after) эмпирически.
2. Точное поведение «30-second preview» не детализировано: что происходит, если обновления прекратились (драфт просто исчезает?), и отсчитывается ли 30 секунд от последнего обновления.
3. `keep_on_stop`: «draft will still disappear after a short time or if the bot sends a message» — «short time» не определено; гарантированное сохранение только через отправку нового сообщения.
4. sendMessageDraft/sendRichMessageDraft принимают только числовой chat_id приватного чата; не описано поведение в группах/каналах (очевидно, ошибка).
5. MessageGenerationStopped не содержит ни user (кто нажал Stop — в приватном чате очевидно), ни business_connection_id; корреляция только по chat+draft_id(+thread).
6. Документация aiogram живёт под URL `/dev-3.x/`, но помечена как «aiogram 3.31.0 documentation» — при цитировании учитывать, что dev-3.x может опережать релиз (здесь проверено соответствие тегу v3.31.0 для ключевого метода).
7. Не документировано, можно ли «закрыть» драфт без отправки финального сообщения (видимо, нет — ждать истечения или отправить сообщение).

## Рекомендация проекту

### Fallback-цепочка стриминга (на каждую генерацию)

1. **Tier 1 — Rich draft:** `bot.send_rich_message_draft(chat_id, draft_id, InputRichMessage(markdown=partial), can_stop=True, keep_on_stop=True)`, клиентский throttle (стартово ~1 обновление/сек, backoff по 429). Пустой старт — `InputRichMessage(blocks=[InputRichBlockThinking(...)])` или `<tg-thinking>`.
   - Риск: partial markdown может быть синтаксически неполным (незакрытый ``` fence, `|`-таблица на середине) → TelegramBadRequest; смягчение: стримить «безопасный» снапшот (закрывать блоки) или временно переходить на plain.
2. **Tier 2 — классический draft:** при ошибке/недоступности Tier 1 → `bot.send_message_draft(chat_id, draft_id, text=..., parse_mode=None или HTML, can_stop=True)`. Тот же draft_id продолжает анимированно обновляться. Финал — `bot.send_message(...)` (4096 символов; длиннее — разбивка).
3. **Tier 3 — throttled edit:** классический `send_message` + `edit_message_text` с троттлингом (текущий рабочий подход; edit_message_text кстати поддерживает rich_message=..., если сообщение уже отправлено как rich).
4. **Финализация:** `bot.send_rich_message(chat_id, InputRichMessage(markdown=final))` (persist), далее прикрепление reply_markup/кнопок при необходимости. Если rich не прошёл — `send_message` с parse_mode.
5. **Отмена:** `@router.stopped_message_generation` → найти активную генерацию по `(event.chat.id, event.draft_id)` → отменить asyncio-задачу/HTTP-стрим к провайдеру LLM; если keep_on_stop не поможет сохранить частичный текст — отправить его обычным сообщением. Polling: хендлер зарегистрирован → aiogram сам добавит тип в allowed_updates; webhook: добавить "stopped_message_generation" в allowed_updates явно.

### Raw-вызов Bot API из aiogram (запасной вариант; сейчас не требуется)

Если понадобится метод/параметр, которого нет в установленной версии aiogram:

- Объявить свой класс-наследник `aiogram.methods.base.TelegramMethod[T]`: задать `__api_method__ = "methodName"`, `__returning__ = <тип результата>` и pydantic-поля параметров; вызвать `await bot(MyMethod(...))` (Bot.__call__ → `bot.session(bot, method, timeout=...)` — уйдёт через ту же сессию, прокси, middleware сессии и лимиты, что и обычные вызовы).
- Для десериализации ответа использовать существующие типы aiogram (например, `TelegramMethod[bool]`) или `model_validate` кастомного типа.
- Это тот же механизм, которым сгенерирован сам aiogram (кодогенерация «butcher»), поэтому поведение идентично нативным методам; минусы: ручная поддержка сигнатуры, нет шортката в Bot и IDE-докстрингов.
- Альтернатива «совсем вручную» — прямой POST на `session.api.api_url(token, method)` через `bot.session` (AiohttpSession), но это обход типизации и не рекомендуется, пока хватает TelegramMethod.

## Открытые вопросы

1. Эмпирический потолок частоты обновлений draft (подобрать throttle на тестовом боте; следить за 429/retry_after).
2. Поведение клиентов на partial markdown в sendRichMessageDraft: насколько терпим парсер к незакрытым блокам; нужен ли «санитайзер» снапшота.
3. Совместимость клиентов: какие версии Telegram-клиентов рендерят rich messages и кнопку Stop (документация не указывает минимальные версии).
4. Работает ли стриминг-драфт в приватных чатах с включёнными топиками (message_thread_id) — параметр есть, поведение не задокументировано детально.
5. TTL драфта после keep_on_stop («short time») — замерить.
6. Нужен ли учёт allow_paid_broadcast/лимитов при массовой финализации sendRichMessage (для проекта с одним owner — не критично).
