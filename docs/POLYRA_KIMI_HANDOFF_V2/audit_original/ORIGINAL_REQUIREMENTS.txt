Ты являешься lead/principal engineer и главным агентом разработки.

Нужно спроектировать и полностью реализовать production-ready
Telegram AI Assistant.

Это НЕ простой Telegram-бот из одного файла.
По функциональности это персональное приложение уровня Gemini/ChatGPT,
но основной интерфейс разговора находится непосредственно в Telegram.

Все настройки, управление чатами и админ-панель находятся в Telegram Mini App.

==================================================
0. КРИТИЧЕСКОЕ ПРАВИЛО РАБОТЫ
==================================================

НЕ начинай сразу писать приложение.

Сначала:
1. исследуй актуальную документацию;
2. создай локальную документацию проекта;
3. спроектируй архитектуру;
4. спроектируй БД;
5. составь план;
6. распредели работу между субагентами;
7. только после этого приступай к реализации.

ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ СУБАГЕНТОВ, если среда позволяет их создавать.

Не делай всё последовательно одним главным агентом.

Главный агент:
- принимает архитектурные решения;
- распределяет задачи;
- не позволяет субагентам одновременно менять одни и те же файлы;
- интегрирует изменения;
- запускает тесты;
- исправляет integration problems;
- контролирует соответствие ТЗ.

Рекомендуемые субагенты:

AGENT A — Telegram Bot / aiogram / streaming
AGENT B — Telegram Mini App frontend + auth
AGENT C — Gemini provider + Gemini project pool
AGENT D — Alibaba provider + model capabilities + tools
AGENT E — chats / context / memory / compaction
AGENT F — web search / open_url / SSRF security
AGENT G — PostgreSQL / migrations / repository layer
AGENT H — tests / security review / integration review

Одновременно запускай максимум 4 независимых субагента.

Исследовательские субагенты сначала должны записывать результаты в:

.agents/reports/

Например:

.agents/reports/telegram.md
.agents/reports/gemini.md
.agents/reports/alibaba.md
.agents/reports/search.md

После исследования основной агент принимает решения.

Не позволяй нескольким агентам независимо проектировать один и тот же
provider или переписывать общие core abstractions.

==================================================
1. ДОКУМЕНТАЦИЯ ПЕРЕД РАЗРАБОТКОЙ
==================================================

Перед написанием production code изучи ТОЛЬКО актуальные на момент работы
официальные документы.

Обязательно изучить:

Telegram:
- Telegram Bot API
- Telegram Mini Apps
- Telegram Bot API changelog
- официальный aiogram repository
- официальный aiogram Web App example
- aiogram WebApp initData validation

Google:
- Gemini API Models
- Gemini Thinking
- Gemini Rate Limits
- Gemini Interactions API
- google-genai Python SDK
- token counting
- function calling
- image input

Alibaba Cloud Model Studio:
- OpenAI-compatible Chat Completions
- model pages для qwen3.8-flash
- qwen3.8-max
- deepseek-v4.1-flash
- glm-5.3
- kimi-k3
- function calling
- thinking/reasoning parameters
- Kimi dynamic tool loading

Search providers:
- Serper
- SerpApi Google AI Overview
- Brave Search
- Jina Search/Reader

Создай:

docs/vendor/TELEGRAM_BOT_API.md
docs/vendor/TELEGRAM_MINI_APPS.md
docs/vendor/AIOGRAM.md
docs/vendor/GEMINI.md
docs/vendor/ALIBABA.md
docs/vendor/SEARCH_BACKENDS.md

В каждом файле указать:
- дату проверки;
- текущую версию API/framework;
- какие параметры реально используются проектом;
- ссылки/названия официальных источников;
- обнаруженные противоречия документации;
- какое решение принято проектом.

Если документация Alibaba противоречит сама себе —
НЕ угадывать.

Создать runtime capability probe и проверить API.

==================================================
2. STACK
==================================================

Backend:
- Python 3.12+
- asyncio
- aiogram 3.31.x или более новая совместимая 3.x версия
- FastAPI
- Uvicorn
- httpx
- OpenAI Python SDK
- google-genai
- PostgreSQL 16+
- SQLAlchemy 2 async
- Alembic
- pgvector optional
- Pydantic v2
- pytest
- Ruff
- mypy или pyright

Frontend Mini App:
- React
- TypeScript
- Vite
- Telegram WebApp JS API

Deployment:
- Docker
- Docker Compose
- reverse proxy Caddy или nginx
- HTTPS обязателен для Mini App

НЕ добавляй Redis без реальной необходимости.

Проект рассчитан на одного владельца и небольшое число приглашённых людей.
PostgreSQL достаточно для locks, state и persistence.

==================================================
3. OWNER И ДОСТУП
==================================================

Главный владелец:

Telegram numeric user_id:
795063564

Это абсолютный owner.

Username НИКОГДА не использовать как security identity.
Username может измениться.

Каждый incoming update проверяется по Telegram numeric user ID.

Доступ имеют:
1. owner;
2. пользователи с активным access_grant.

Поддержать:

- permanent access;
- access until datetime;
- suspend;
- revoke;
- ban;
- unban.

У пользователя могут быть индивидуальные permissions:

- список разрешённых моделей;
- можно/нельзя Web Search;
- можно/нельзя memory;
- число одновременных генераций;
- optional requests/day;
- optional token limit.

Проверка permissions должна выполняться server-side.

Скрытая кнопка в интерфейсе НЕ считается защитой.

==================================================
4. ОБЩАЯ АРХИТЕКТУРА
==================================================

Telegram Bot
    |
    v
Access Middleware
    |
    v
Chat Service
    |
    v
Context Builder
    |
    v
LLM Router
    |
    +--> GeminiProvider
    |
    +--> AlibabaProvider
    |
    v
Tool Engine
    |
    +--> web_search
    +--> open_url
    +--> set_chat_title
    +--> remember
    +--> forget_memory
    |
    v
Persistence

Telegram handlers не должны знать:
- HTTP API Gemini;
- HTTP API Alibaba;
- quota implementation;
- SQL details;
- Search provider details.

==================================================
5. PROJECT STRUCTURE
==================================================

Начальная рекомендуемая структура:

app/
    main.py
    config.py

    bot/
        dispatcher.py
        commands.py
        routers/
            chat.py
            photos.py
            commands.py
            stop.py
        middleware/
            access.py
            errors.py
        streaming/
            rich_draft.py
            fallback_stream.py

    api/
        app.py
        auth.py
        dependencies.py
        routes/
            me.py
            settings.py
            chats.py
            memory.py
            admin_users.py
            admin_access.py
            admin_models.py
            admin_gemini.py
            admin_providers.py
            admin_search.py
            admin_stats.py
            admin_system.py

    llm/
        base.py
        events.py
        router.py
        registry.py
        capabilities.py

        providers/
            gemini.py
            alibaba.py

        gemini/
            pool.py
            quota.py
            errors.py

        tools/
            registry.py
            runner.py
            schemas.py
            chat_title.py
            memory.py

    context/
        builder.py
        compactor.py
        token_budget.py

    memory/
        extractor.py
        retriever.py
        deduplicator.py

    search/
        base.py
        manager.py
        serper.py
        serpapi_ai_overview.py
        brave.py
        jina.py
        playwright_google.py
        reader.py
        security.py

    services/
        chats.py
        messages.py
        users.py
        access.py
        generation.py
        settings.py

    db/
        base.py
        session.py
        models/
        repositories/
        migrations/

    security/
        crypto.py
        secrets.py
        ssrf.py

    observability/
        logging.py
        metrics.py

miniapp/
    src/
        app/
        api/
        components/
        pages/
        telegram/
        admin/
        styles/

tests/
    unit/
    integration/

scripts/
    smoke_providers.py
    import_gemini_keys.py

docs/
    vendor/

.agents/
    reports/

PLAN.md
TASKS.md
DECISIONS.md
KNOWN_ISSUES.md
README.md
.env.example
Dockerfile
docker-compose.yml

Ты можешь улучшить структуру, но НЕ превращай всё в один модуль.

==================================================
6. ЕДИНЫЙ LLM INTERFACE
==================================================

Создай единый abstraction:

LLMProvider

который умеет:

stream_chat(request) -> AsyncIterator[LLMEvent]

LLMRequest содержит:

- model
- messages
- system_prompt
- image parts
- thinking setting
- tools
- tool_choice
- max_output_tokens
- metadata
- cancellation token

Унифицированные события:

TextDelta
ReasoningDelta
ToolCall
ToolResult
Usage
Error
Done

ВАЖНО:

ReasoningDelta НИКОГДА не отправлять пользователю.

Reasoning может быть принят от provider,
но пользователь видит ТОЛЬКО final answer.

Не смешивать reasoning_content с content.

==================================================
7. MODEL REGISTRY
==================================================

Все особенности моделей хранятся централизованно.

ModelDefinition:

- provider
- model_id
- display_name
- enabled
- input_modalities
- max_context
- max_output
- function_calling
- structured_output
- thinking_modes
- supports_images
- native_web_search
- internal_only
- provider_options

Telegram handlers НЕ должны содержать:
"if model == ..."

==================================================
8. GEMINI
==================================================

Основные пользовательские модели:

gemini-3.8-flash
gemini-3.7-flash
gemini-3.6-flash

Thinking:

gemini-3.8-flash:
LOW
MEDIUM
HIGH

gemini-3.7-flash:
LOW
MEDIUM
HIGH

gemini-3.6-flash:
MINIMAL
LOW
MEDIUM
HIGH

Использовать thinking_level.

Не использовать legacy thinking_budget для Gemini 3.x.

Gemini должен работать через HTTPS custom proxy,
указанный владельцем проекта:

extraordinary-piroshki-4e3b92.netlify.app

Это пользовательский proxy и он контролируется владельцем проекта.

НЕ заменять его самовольно прямым Google endpoint.

Проверить актуальный google-genai SDK.

Сначала попытаться использовать HttpOptions/base_url.

Если конкретный endpoint Interactions API через SDK с custom base URL
работает некорректно — создать аккуратный raw HTTP adapter через httpx,
НО всё равно использовать тот же custom proxy.

Создать integration smoke test.

==================================================
9. GEMINI PROJECT POOL
==================================================

Есть примерно 30 API keys.

ВАЖНО:
они принадлежат РАЗНЫМ Google AI Studio projects.

Поэтому каждый проект — независимый quota bucket.

Таблица gemini_projects:

id
name
encrypted_api_key
rotation_order
enabled
health_status
cooldown_until
last_success_at
last_error_at
last_error_code
created_at

Quota учитывается как:

(project_id, model_id)

Для основных моделей стартовые пользовательские лимиты:

RPM = 4
TPM = 249999
RPD = 19

Эти значения НЕ hardcode.

Они являются initial config и должны редактироваться через admin Mini App.

Реализовать:
- minute usage;
- daily usage;
- optional token reservation;
- cooldown;
- last error;
- health.

Алгоритм выбора:

1. взять следующий eligible project;
2. проверить enabled;
3. проверить cooldown;
4. проверить локально известную quota;
5. зарезервировать запрос;
6. сделать API call;
7. после ответа reconcile actual usage.

Но дополнительно владелец хочет практичное failover-поведение:

если проект/key получает retryable provider error —
перейти к следующему проекту для ТОЙ ЖЕ САМОЙ МОДЕЛИ.

НИКОГДА не менять модель автоматически.

Классификация ошибок:

400 invalid request:
- НЕ перебирать 30 keys;
- вернуть ошибку;
- потому что запрос вероятно сломан для всех проектов.

401 / invalid credential:
- disable или mark unhealthy credential;
- перейти на следующий.

403 credential/project/quota issue:
- поставить cooldown;
- попробовать следующий project.

429:
- cooldown project+model;
- попробовать следующий project.

5xx:
- небольшой bounded retry;
- затем следующий project.

network timeout:
- bounded retry;
- затем следующий project.

content/safety rejection:
- НЕ перебирать весь pool.

Нельзя допустить бесконечный цикл.

Максимум один полный проход по доступным проектам на один request.

Если pool исчерпан:
вернуть нормальное user-facing сообщение.

==================================================
10. ВНУТРЕННЯЯ GEMINI 3.5 FLASH-LITE
==================================================

Добавить hidden/internal model:

gemini-3.5-flash-lite

Она НЕ обязана показываться обычному пользователю в выборе моделей.

Использовать её как дешёвого внутреннего subagent:

1. context summarization;
2. automatic memory extraction;
3. chat title generation fallback;
4. structured metadata extraction.

Настройки:

context summary:
thinking_level = MEDIUM

memory extraction:
thinking_level = MEDIUM

title:
thinking_level = LOW

Все значения должны быть configurable.

Для неё использовать тот же GeminiProjectPool,
но отдельные quota policies по model_id.

Если quota limits неизвестны —
НЕ придумывать их.
Оставить configurable/unlimited-local-accounting mode
и реагировать на реальные provider rate limit errors.

==================================================
11. ALIBABA CLOUD
==================================================

Использовать ТОЛЬКО OpenAI-compatible endpoint,
заданный владельцем.

HTTPS host/path:

dashscope.aliyuncs.com/compatible-mode/v1

НЕ:
- мигрировать на Workspace URL;
- использовать Singapore URL;
- использовать international URL;
- автоматически переписывать endpoint.

Это hard project requirement.

API key находится в secrets storage.

Владелец сообщает, что его credential практически работает
без расходуемой квоты.

Поэтому для Alibaba НЕ делать proactive RPM/TPM/RPD quota accounting.

Однако обязательно обрабатывать:
- network error;
- timeout;
- 429;
- 5xx;
- auth errors;
- malformed response.

Не предполагай, что endpoint физически никогда не может вернуть ошибку.

==================================================
12. ALIBABA MODELS
==================================================

Поддержать:

qwen3.8-flash
qwen3.8-max
deepseek-v4.1-flash
glm-5.3
kimi-k3

QWEN 3.8:

UI:
OFF
LOW
MEDIUM
MAX

Mapping:

OFF:
enable_thinking=false

LOW:
reasoning_effort=low

MEDIUM:
reasoning_effort=medium

MAX:
reasoning_effort=xhigh

Не устанавливать одновременно reasoning_effort
и thinking_budget.

По умолчанию:
preserve_thinking=false

Reasoning не показывать.

--------------------------------

DEEPSEEK V4.1 FLASH:

UI:
OFF
LOW
HIGH
MAX

Использовать актуальные параметры Alibaba docs.

LOW -> reasoning_effort=low
HIGH -> reasoning_effort=high
MAX -> reasoning_effort=max

Для OFF сначала проверить реальный endpoint capability.

Если OFF не поддерживается —
не показывать OFF в Mini App.

--------------------------------

GLM 5.3:

UI:
LOW
HIGH
MAX

reasoning_effort:
low
high
max

Thinking нельзя отключать.

Использовать:
clear_thinking=true
если это соответствует актуальной документации и runtime test.

Reasoning пользователю не показывать.

--------------------------------

KIMI K3:

Использовать Chat Completions API.

НЕ предполагать поддержку Responses API,
пока runtime/docs не подтверждают.

Поддержать:
- text;
- image;
- streaming;
- Function Calling;
- Structured Output где нужно;
- Dynamic Tool Loading optional.

thinking_budget для kimi-k3 НЕ использовать.

Документация Alibaba по настройкам thinking сейчас может быть
внутренне противоречивой.

Поэтому реализовать startup/smoke capability probe:

проверить:
reasoning_effort=low
reasoning_effort=high
reasoning_effort=max

и отдельно проверить,
принимается ли enable_thinking=false.

Не тратить на probe большие prompts.

Capability result кешировать.

Mini App показывает ТОЛЬКО реально поддерживаемые варианты.

До успешного probe безопасный fallback:
thinking enabled / provider default.

Reasoning НЕ показывать пользователю.

==================================================
13. НИКАКОГО CROSS-MODEL FALLBACK
==================================================

Это принципиальное требование.

Если пользователь выбрал:

Gemini 3.8 Flash

бот может сменить Google PROJECT/API KEY,
но остаётся на Gemini 3.8 Flash.

Если Alibaba model упал —
не надо молча отправлять запрос в другую модель.

Если Kimi не ответил —
не переключаться на Qwen.

Если Qwen не ответил —
не переключаться на Gemini.

Сообщить пользователю об ошибке и позволить повторить.

==================================================
14. NETWORK ROUTING
==================================================

Не менять default route всей машины без необходимости.

Сделать независимые HTTP transports.

Telegram:
configurable proxy/WARP transport.

Gemini:
custom Gemini proxy, заданный владельцем.

Alibaba:
DIRECT network connection.

Для Alibaba HTTP client:
trust_env = false

чтобы он случайно не подхватил:
HTTP_PROXY
HTTPS_PROXY
ALL_PROXY

от Telegram/Gemini.

Network configuration вынести в settings.

==================================================
15. TELEGRAM BOT
==================================================

Основной Telegram chat используется ТОЛЬКО для общения.

Команды:

/start
/new
/chats
/settings
/help

/admin разрешён только owner и открывает Mini App admin section.

На /start:
- проверить access;
- показать краткое приветствие;
- установить menu button Mini App.

Поддержать:
- text;
- photo + optional caption.

Пока НЕ нужно реализовывать:
- PDF;
- audio;
- video;
- arbitrary documents.

Но MessagePart architecture должна позволять добавить это позже.

==================================================
16. TELEGRAM STREAMING
==================================================

Использовать современные возможности Telegram Bot API.

При генерации использовать Rich Message Draft,
если текущий Bot API/aiogram это поддерживает.

Показывать только final answer stream.

НЕ показывать:
thinking;
reasoning_content;
chain-of-thought.

Использовать can_stop=true.

Обрабатывать update:
stopped_message_generation.

Если пользователь нажал Stop:

1. найти generation task;
2. выставить cancellation token;
3. закрыть provider stream;
4. прекратить tool loop;
5. если уже есть видимый partial answer —
   сохранить его как обычное сообщение;
6. generation_run пометить cancelled.

Не отправлять update Telegram на каждый token.

Создать throttling/buffer:
например обновление draft каждые несколько сотен миллисекунд
или при накоплении разумного числа символов.

После окончания:
draft -> persistent Rich Message.

Если Rich Message Draft недоступен:
fallback на обычный sendMessageDraft/sendMessage.

Если и он недоступен:
fallback на throttled edit/send behavior.

==================================================
17. PHOTOS
==================================================

При получении фото:

- сохранить Telegram file_id;
- скачать highest useful resolution;
- определить MIME;
- проверить size limit;
- сформировать ImagePart;
- передать модели.

Message content:

[
  TextPart,
  ImagePart
]

Не base64-хранить огромные изображения в PostgreSQL.

Хранить Telegram file_id и metadata.

Поддержать image-capable модели.

Если выбранная модель не поддерживает image:
не терять фото молча.

Вернуть понятное сообщение и показать модели,
которые способны обработать изображение.

==================================================
18. CHAT SYSTEM
==================================================

Каждый пользователь имеет несколько chats.

Операции:

new
list
open
rename
archive
delete

В будущем export,
но архитектуру предусмотреть.

Chat fields:

id
owner_user_id
title
model_id
thinking_setting
web_enabled
memory_enabled
system_prompt_override
created_at
updated_at
archived_at

Каждый запрос привязан к конкретному chat_id.

Разрешить только одну активную generation на один chat,
чтобы история не ломалась из-за race conditions.

==================================================
19. MESSAGES
==================================================

messages:

id
chat_id
role
status
created_at
provider
model_id
generation_run_id
token metadata

message_parts:

id
message_id
type
position
text
telegram_file_id
mime_type
metadata_json

Part types на старте:

text
image
tool_call
tool_result

Архитектура должна позволять позже добавить:
audio
video
document

==================================================
20. CHAT TITLE
==================================================

У нового чата title отсутствует.

Во время первого normal generation можно дать модели internal tool:

set_chat_title(title)

Требования:
- 3-7 слов;
- без кавычек;
- отражает тему разговора.

Tool доступен только пока title IS NULL.

Если выбранная модель tool не вызвала
или вызов failed:

fallback background call:

gemini-3.5-flash-lite
thinking LOW
structured JSON

После установки title tool больше не добавлять.

==================================================
21. ИСТОРИЯ И CONTEXT COMPACTION
==================================================

НЕЛЬЗЯ каждый раз бесконечно отправлять полный chat history.

PostgreSQL хранит ВСЮ исходную историю.

Context для LLM строится отдельно:

SYSTEM
+
LONG TERM MEMORIES
+
CHAT SUMMARY
+
RECENT RAW MESSAGES
+
CURRENT MESSAGE

Создать TokenBudgetManager.

Он учитывает:
- context window текущей модели;
- reserved output tokens;
- system prompt;
- tools;
- images;
- safety margin.

Compaction запускать только когда нужно,
а не после каждого сообщения.

Не суммаризировать самые свежие сообщения.

Например сохранять raw recent window,
а старый сегмент сворачивать.

Все thresholds configurable.

==================================================
22. STRUCTURED CHAT SUMMARY
==================================================

Использовать hidden:

gemini-3.5-flash-lite
thinking MEDIUM

Summary должен быть structured JSON.

Пример логической структуры:

{
  "conversation_summary": "...",
  "important_facts": [],
  "decisions": [],
  "open_threads": [],
  "user_preferences": [],
  "entities": []
}

Summary обязан:
- не выдумывать факты;
- сохранять важные числа;
- сохранять имена;
- сохранять решения;
- сохранять незакрытые задачи;
- не дублировать мусор.

Если JSON invalid:
один repair attempt.

Не запускать бесконечный repair loop.

==================================================
23. AUTOMATIC MEMORY
==================================================

Memory включена автоматически.

После подходящих conversation turns
фоновый memory extractor анализирует новый материал.

Использовать:
gemini-3.5-flash-lite
thinking MEDIUM

Запоминать только долговременно полезную информацию:

- стабильные preferences;
- информацию о пользовательских проектах;
- важные договорённости;
- устойчивые факты;
- инструкции, которые пригодятся позже.

НЕ запоминать:
- обычные одноразовые вопросы;
- случайные поисковые запросы;
- промежуточный мусор;
- содержимое tool outputs без необходимости.

Memory record:

id
user_id
text
normalized_text
category
importance
created_at
updated_at
last_used_at
source_chat_id
source_message_id
embedding optional

Перед вставкой:
deduplicate / update existing memory.

Memory retrieval учитывать:
- relevance;
- importance;
- recency.

Если semantic embedding layer усложняет первый запуск,
реализовать abstraction:

MemoryRetriever

с:
1. PostgreSQL FTS fallback;
2. optional pgvector semantic retrieval.

Для semantic embeddings желательно использовать
маленькую локальную multilingual model,
а не тратить основной LLM API на embeddings.

Mini App должен позволять:
- посмотреть memories;
- изменить;
- удалить;
- выключить память для чата.

==================================================
24. TOOL ENGINE
==================================================

Создать generic async ToolRegistry.

ToolDefinition:
- name
- description
- JSON schema
- timeout
- required_permission
- enabled
- max_result_size

Начальные tools:

web_search
open_url
set_chat_title
remember
forget_memory

Tool loop:

1. model requests tool;
2. validate arguments;
3. permission check;
4. execute;
5. sanitize result;
6. append ToolResult;
7. continue same generation.

Ограничить:

MAX_TOOL_ITERATIONS = configurable
default около 8

Tool timeout обязателен.

Каждый call писать в tool_calls.

НЕ предоставлять:
shell execution;
arbitrary filesystem access;
arbitrary Python execution.

==================================================
25. KIMI DYNAMIC TOOL LOADING
==================================================

Если актуальная документация и smoke tests подтверждают
Dynamic Tool Loading у kimi-k3:

реализовать его внутри AlibabaProvider.

Generic core не должен знать об этой особенности.

Core tools:
web_search
open_url

могут быть видимыми сразу.

Редкие tools могут подключаться динамически.

Для остальных моделей использовать обычный tools/function calling mechanism.

==================================================
26. WEB SEARCH ABSTRACTION
==================================================

НЕ привязывать web_search к одному сервису.

Создать:

SearchBackend

async search(query, options) -> SearchResult[]

SearchResult:
title
url
snippet
source
published_at optional
metadata

SearchManager имеет ordered list backends.

Admin Mini App может:
- включить backend;
- выключить backend;
- изменить priority;
- проверить health;
- установить masked API key.

==================================================
27. SERPER
==================================================

Добавить SerperBackend.

Использовать для обычного быстрого Google SERP.

Это предпочтительный primary backend
для первой версии.

Поддержать:
- query;
- language;
- country;
- number results;
- safe timeout.

==================================================
28. GOOGLE AI OVERVIEW
==================================================

Создать отдельный:

SerpApiGoogleAIOverviewBackend

Он НЕ заменяет обычный search.

Он предназначен для получения Google AI Overview,
когда эта функция доступна для запроса.

Result должен сохранять:
- текст AI Overview;
- sections;
- references;
- source URLs.

web_search может иметь mode:

normal
ai_overview
auto

AUTO:

1. ordinary search;
2. при необходимости AI Overview;
3. вернуть структурированные references.

Не выдавать текст AI Overview как источник истины без references.

==================================================
29. JINA
==================================================

Добавить:

JinaSearchBackend optional
JinaReaderBackend

Jina Reader использовать как удобный путь
преобразования страницы в LLM-friendly text.

Но иметь direct HTTP extraction fallback,
чтобы проект не зависел полностью от Jina.

==================================================
30. BRAVE
==================================================

Добавить BraveSearchBackend как optional fallback.

Если API key отсутствует —
backend считается disabled,
а не вызывает startup crash.

==================================================
31. EXPERIMENTAL GOOGLE BROWSER
==================================================

Создать OPTIONAL:

PlaywrightGoogleBackend

disabled by default.

Он может:
- запустить Chromium;
- открыть Google Search;
- выполнить обычный search;
- разобрать обычные search results;
- попытаться прочитать AI Overview.

Но:

НЕ реализовывать CAPTCHA bypass.
НЕ реализовывать stealth fingerprint spoofing.
НЕ реализовывать автоматическое решение challenge.
НЕ логиниться в Google account.

При CAPTCHA / unusual traffic:
- abort;
- mark backend cooldown;
- перейти на следующий SearchBackend.

Это experimental fallback,
а не основной production search.

==================================================
32. OPEN_URL
==================================================

Создать tool:

open_url(url)

Pipeline:

1. validate scheme;
2. DNS resolve;
3. SSRF checks;
4. fetch;
5. content type check;
6. max bytes;
7. extract readable text;
8. truncate according to tool token budget.

Разрешить только:
http
https

Блокировать:
localhost
127.0.0.0/8
private IPv4
private IPv6
link-local
cloud metadata endpoints
file://
ftp://
gopher://
redirect into private network

Проверять IP ПОСЛЕ DNS resolution
и после каждого redirect.

Web content считать UNTRUSTED DATA.

Инструкция на сайте вида:
"ignore previous instructions"
не является system instruction.

==================================================
33. SEARCH CITATIONS
==================================================

Tool result должен сохранять URL references.

Когда ответ использовал Web Search,
модель должна указывать источники.

Telegram final message желательно формировать так:

основной ответ

Источники:
1. Title
2. Title
3. Title

При Rich Messages можно использовать нормальные clickable links/buttons.

Не заставлять пользователя смотреть огромный dump search results.

==================================================
34. MINI APP
==================================================

Mini App — основной configuration/admin UI.

НЕ делай отдельную внешнюю web-панель.

Это Telegram Mini App.

Frontend:
React + TypeScript + Vite.

Использовать Telegram theme variables.

Должен нормально выглядеть:
- Telegram Desktop;
- Android;
- iOS.

UI mobile-first.

==================================================
35. MINI APP AUTH
==================================================

Frontend получает:

Telegram.WebApp.initData

и отправляет RAW initData backend.

Backend ОБЯЗАН проверить Telegram signature.

Не доверять:
initDataUnsafe.user.id

пока initData не validated server-side.

Проверять:
- signature;
- auth_date freshness;
- user id;
- active access.

После validation можно выдать короткоживущую server session/token.

Owner определяется только как:

user_id == 795063564

Все admin API endpoints дополнительно проверяют owner role.

==================================================
36. MINI APP USER UI
==================================================

Обычный пользователь видит:

Главная
Чаты
Модель
Thinking
Интернет
Память
Настройки

MODEL:

показывать только разрешённые ему модели.

THINKING:

варианты автоматически зависят от ModelRegistry capability.

WEB:

Off
Auto / On

MEMORY:

On / Off для текущего чата.

CHATS:

- list
- rename
- archive
- delete
- switch current chat

==================================================
37. MINI APP ADMIN UI
==================================================

Только owner видит ADMIN.

Разделы:

Dashboard
Users
Access
Models
Gemini Pool
Alibaba
Web Search
Memory
System
Stats
Audit Log

--------------------------------

USERS:

поиск:
Telegram user_id
username

Показывать:
id
username
first seen
last seen
status
access expiration

Actions:

Grant
Extend
Permanent
Suspend
Revoke
Ban
Unban

--------------------------------

MODEL PERMISSIONS:

Для выбранного пользователя:

checkbox:
Gemini 3.8 Flash
Gemini 3.7 Flash
Gemini 3.6 Flash
Qwen 3.8 Flash
Qwen 3.8 Max
DeepSeek V4.1 Flash
GLM 5.3
Kimi K3

--------------------------------

GEMINI POOL:

показывать:

project name
masked key
enabled
health
rotation position
current cooldown
last success
last error

per-model:
RPM
TPM
RPD

Кнопки:

Add
Bulk Import
Enable
Disable
Move Up
Move Down
Test
Reset Local Counters

Bulk import должен позволить загрузить 30 keys.

Полный ключ после сохранения больше не возвращать frontend.

--------------------------------

ALIBABA:

masked key
provider status
model health
base endpoint

Base endpoint display read-only,
потому что это project requirement.

Кнопка:
Run Smoke Test

--------------------------------

WEB SEARCH:

Serper
SerpApi AI Overview
Brave
Jina
Playwright experimental

Для каждого:
Enabled
Priority
Masked API key
Health
Test query

--------------------------------

SYSTEM:

Default system prompt
Default model
Default thinking
Tool limits
Context limits
Memory config

==================================================
38. DATABASE
==================================================

Минимальные entities:

users
access_grants
user_model_permissions
user_settings

chats
messages
message_parts
chat_summaries
memories

gemini_projects
quota_policies
quota_minute_usage
quota_daily_usage

provider_credentials
provider_health

generation_runs
tool_calls

search_backend_configs
system_settings
audit_log

Использовать UUID для внутренних public-facing entity IDs,
где это разумно.

Telegram user_id:
BIGINT.

==================================================
39. SECRETS
==================================================

НЕ хранить реальные secrets plaintext в git.

.env:
BOT_TOKEN
MASTER_ENCRYPTION_KEY
DATABASE_URL
и bootstrap secrets.

Provider API keys, если хранятся в DB:
encrypt at rest.

Использовать authenticated encryption:
например AES-GCM/Fernet-equivalent.

Master key:
только environment.

Mini App показывает:

abcd...WXYZ

а не полный secret.

Логи обязаны redacted:
Authorization headers
API keys
Telegram bot token
session tokens

==================================================
40. AUDIT LOG
==================================================

Все owner/admin operations писать в audit_log:

actor_user_id
action
target_type
target_id
metadata
created_at

Например:

ACCESS_GRANTED
ACCESS_REVOKED
USER_BANNED
MODEL_PERMISSION_CHANGED
GEMINI_KEY_ADDED
GEMINI_KEY_DISABLED
SEARCH_BACKEND_CHANGED
SYSTEM_PROMPT_UPDATED

==================================================
41. GENERATION RUNS
==================================================

Для каждой генерации сохранять:

request id
chat id
user id
provider
model
thinking setting
started_at
first_token_at
finished_at
status
input_tokens
output_tokens
reasoning_tokens if provider reports them
tool_calls count
selected gemini project
error category
error code

statuses:

queued
running
completed
cancelled
failed

==================================================
42. OBSERVABILITY
==================================================

Admin stats:

requests/day
requests/model
token usage
average latency
time-to-first-token
error rate
429 count
Gemini project usage
Gemini project health
tool usage
search backend usage

Не собирать reasoning text ради статистики.

Достаточно reasoning token count,
если provider возвращает его.

==================================================
43. CONCURRENCY
==================================================

Одна активная generation на один chat.

При race:
не смешивать две истории.

Использовать:
async locks
и/или PostgreSQL advisory locks.

После restart stale generation runs:
mark aborted/failed.

==================================================
44. ERROR UX
==================================================

Не показывать пользователю raw traceback.

Пример:

"Не удалось получить ответ от Kimi K3.
Провайдер вернул временную ошибку.
Можно повторить запрос."

Owner может открыть подробности в Admin -> Logs.

Для Gemini pool можно показать:

"Все Gemini projects для этой модели сейчас недоступны."

Но НЕ раскрывать:
keys;
internal URLs;
stack traces;
database details.

==================================================
45. PROVIDER CAPABILITY PROBE
==================================================

Создать:

scripts/smoke_providers.py

Он делает минимальные дешёвые requests и проверяет:

Gemini:
- each model availability;
- thinking levels;
- image capability;
- custom proxy.

Alibaba:
- model existence;
- basic text;
- streaming;
- reasoning settings;
- image where relevant;
- function calling.

Не запускать автоматически 30 дорогостоящих тестов при каждом startup.

Результаты health check кешировать.

Capability probe Kimi особенно важен
из-за противоречий текущей Alibaba documentation.

==================================================
46. TESTS
==================================================

Обязательные unit tests:

Access:
- owner always allowed;
- expired grant denied;
- permanent grant allowed;
- banned denied;
- model permission enforced.

Mini App:
- valid initData accepted;
- modified initData rejected;
- stale auth rejected;
- non-owner admin API denied.

Gemini pool:
- rotation;
- disabled key;
- cooldown;
- 429 -> next project;
- 401 -> disable/next;
- 400 -> do not rotate all keys;
- all projects unavailable;
- quota reservation race.

Providers:
- thinking mapping;
- hidden reasoning not streamed;
- no model fallback;
- image mapping.

Context:
- recent messages preserved;
- old messages compacted;
- summary does not replace raw DB history;
- token budget respected.

Memory:
- extraction;
- deduplication;
- deletion;
- disabled memory;
- retrieval belongs to correct user only.

Tools:
- invalid JSON args;
- permission denied;
- timeout;
- max loop reached.

Web:
- SSRF localhost blocked;
- private IP blocked;
- redirect-to-private blocked;
- huge document truncated;
- search backend fallback.

Telegram:
- stream buffer;
- stop cancels task;
- partial response persistence;
- photo handling.

==================================================
47. INTEGRATION TESTS
==================================================

Integration tests that consume real APIs
must be DISABLED by default.

Enable only with environment flags.

Например:

RUN_GEMINI_INTEGRATION
RUN_ALIBABA_INTEGRATION
RUN_SEARCH_INTEGRATION

CI unit tests не должны случайно сжигать API quota.

==================================================
48. DEPLOYMENT
==================================================

Docker Compose initial services:

app
postgres

optional:
caddy/nginx

optional profile:
searxng

Mini App production build
собрать multi-stage Docker build.

Не держать Vite dev server в production.

Bot можно запускать через long polling,
а FastAPI одновременно обслуживает Mini App/API.

Не заставлять Telegram webhook работать,
если long polling проще с текущей network configuration.

Позже webhook может быть optional mode.

==================================================
49. TELEGRAM MENU
==================================================

При startup bot должен настроить menu button:

"⚙️ Настройки"

который открывает Mini App.

Для owner Mini App автоматически показывает admin controls.

Также подготовь инструкцию README,
как настроить Main Mini App через BotFather.

==================================================
50. UX
==================================================

В самом чате должно быть минимум лишнего.

Пользователь пишет:

"Объясни квантовую запутанность"

и сразу получает stream ответа.

Не вставлять каждый раз:

Model:
Thinking:
Tokens:
Provider:

если пользователь сам это не включил.

Настройки находятся в Mini App.

Можно добавить небольшие действия под готовым ответом:

Regenerate
Continue
Sources

Но не превращать каждое сообщение в панель управления.

==================================================
51. НИКАКОГО VISIBLE THINKING
==================================================

Это жёсткое требование.

Даже если provider присылает:

reasoning_content
thinking blocks
reasoning deltas

не показывать их пользователю.

Если provider protocol требует reasoning во время текущего tool-call loop,
обрабатывать его внутри adapter.

Не смешивать с final output.

Не сохранять огромный hidden chain-of-thought в обычную messages table.

==================================================
52. КАЧЕСТВО КОДА
==================================================

Требования:

- async end-to-end;
- type hints;
- no blocking HTTP;
- dependency injection;
- small modules;
- no god classes;
- no circular imports;
- explicit provider errors;
- structured logging;
- database transactions;
- migrations;
- comments только там, где логика реально неочевидна.

Запрещено:

app.py на 5000 строк;
один handlers.py на весь проект;
SQL прямо в Telegram handler;
provider HTTP calls прямо в handlers;
hardcoded API keys;
catch Exception: pass;
бесконечный retry loop.

==================================================
53. ПОРЯДОК РЕАЛИЗАЦИИ
==================================================

MILESTONE 0
Research + vendor docs

MILESTONE 1
Project skeleton
config
PostgreSQL
Alembic
users/access

MILESTONE 2
Telegram basic bot
access middleware
/start
text
photos

MILESTONE 3
LLM abstractions
ModelRegistry
Alibaba provider
Gemini provider

MILESTONE 4
Gemini project rotation/quota/failover

MILESTONE 5
streaming + cancellation

MILESTONE 6
chat history + context building
3.5 Flash-Lite compactor
automatic titles

MILESTONE 7
automatic memory

MILESTONE 8
generic tool engine
web_search/open_url

MILESTONE 9
Mini App auth + user settings

MILESTONE 10
Mini App full admin panel

MILESTONE 11
observability
audit
provider health

MILESTONE 12
security review
full tests
Docker deployment
README

После КАЖДОГО milestone:

1. run tests
2. run Ruff
3. run type checker
4. fix errors
5. update TASKS.md
6. update DECISIONS.md if architecture changed
7. commit logical result if git is available
8. только после этого следующий milestone

==================================================
54. SUBAGENT RULES
==================================================

Главный агент ОБЯЗАН использовать subagents для независимых частей.

Перед запуском subagent:
выдать ему конкретный scope.

Плохая задача:
"сделай backend"

Хорошая задача:
"исследуй Telegram Bot API 10.3 streaming и напиши
.agents/reports/telegram_streaming.md, production code не меняй"

После research phase:

Хорошая coding task:
"реализуй app/search/* и tests/unit/test_search_*;
не изменяй llm providers и Telegram handlers"

Каждый subagent должен вернуть:
- изменённые файлы;
- принятые решения;
- тесты;
- известные ограничения.

Главный агент проверяет diff.

Не принимать код subagent вслепую.

==================================================
55. НЕ ОСТАНАВЛИВАТЬСЯ НА ПСЕВДОКОДЕ
==================================================

После архитектурной фазы реально реализуй проект.

Не оставляй:

TODO implement
pass
NotImplementedError

в критических production paths.

Допустимы TODO только для явно future features,
не входящих в текущий scope:
audio
video
documents
payments
public signup

==================================================
56. DEFINITION OF DONE
==================================================

Проект считается готовым, когда:

- owner 795063564 может открыть bot;
- неизвестный user получает denied;
- owner может через Mini App выдать user ID временный доступ;
- приглашённый user после этого может писать боту;
- owner может ограничить ему список моделей;

- Gemini 3.8/3.7/3.6 работают;
- 30 different-project Gemini credentials rotating correctly;
- rate-limited Gemini project автоматически заменяется следующим;
- модель при этом НЕ меняется;

- qwen3.8-flash работает;
- qwen3.8-max работает;
- deepseek-v4.1-flash работает;
- glm-5.3 работает;
- kimi-k3 работает;

- правильные thinking options показываются для каждой модели;
- reasoning никогда не отображается пользователю;

- text chat работает;
- image chat работает;

- ответы streaming;
- Telegram Stop действительно отменяет provider generation;

- chat history сохраняется;
- новый chat получает title;
- старый context compacted через gemini-3.5-flash-lite;
- automatic memory работает;

- web_search доступен моделям через Function Calling;
- ordinary Google search работает;
- AI Overview backend работает при наличии результата;
- open_url безопасно извлекает страницу;
- источники доходят до final response;

- Mini App авторизуется через validated Telegram initData;
- обычный пользователь не видит admin;
- owner имеет полноценную admin panel;

- secrets encrypted/masked;
- migrations работают с пустой DB;
- Docker Compose поднимает приложение;
- unit tests проходят;
- README позволяет развернуть проект с нуля.

==================================================
57. ПЕРВОЕ СООБЩЕНИЕ ОТ ТЕБЯ
==================================================

НЕ начинай ответ с написания десятков production files.

Сначала выведи:

1. краткое понимание задачи;
2. найденные актуальные версии/API;
3. архитектурные риски;
4. proposed directory tree;
5. database entities;
6. provider architecture;
7. Mini App architecture;
8. subagent work split;
9. implementation milestones.

После этого создай:
PLAN.md
TASKS.md
DECISIONS.md

Запусти research subagents.

После получения их результатов —
начинай реализацию milestone 1.

Не спрашивай подтверждения после каждого мелкого шага.
Работай самостоятельно до рабочего результата,
останавливаясь только если обнаружена реально блокирующая проблема.