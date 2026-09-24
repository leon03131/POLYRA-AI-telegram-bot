# Проверенные внешние источники — 24 сентября 2026 года

Это дополнение к первоначальному аудиту, а не новый live-аудит бота.
Ни Telegram, ни Alibaba, ни Gemini с ключами владельца при пересборке пакета не вызывались.
Версия OpenCode Desktop владельца неизвестна: устанавливать её по догадке нельзя.

## OpenCode

[S1] https://opencode.ai/docs/agents/
Проектные Markdown-агенты размещаются в `.opencode/agents/`. Поддерживаются `description`
и `mode: subagent`. Без `model` субагент использует модель вызвавшего primary agent.
Эта ветка документации использует `task` и `permission.task`.

[S2] https://opencode.ai/v2/docs/agents
Ветка v2 описывает `subagent` и другой формат `permissions`: список правил
action/resource/effect. Поэтому примеры разных веток нельзя смешивать в одном конфиге.
Права child agent проверяются отдельно; считать его автоматически ограниченной копией
родителя нельзя.

[S3] https://opencode.ai/docs/config/
Конфигурации JSON/JSONC объединяются по приоритету. Изменения должны сохранять
существующие provider/model/permission настройки пользователя.

[S4] https://opencode.ai/
Официальный продукт имеет desktop-интерфейс и поддерживает параллельные сессии.
Это не проверка конкретной установки пользователя.

**Решение пакета:** восемь проектных описаний агентов с общими полями frontmatter,
без фиксированного `model`, без полной замены opencode.json и без готового разрешения
«allow everything». Название инструмента делегирования и права проверяет главный
агент в реально установленной версии. Ограничение «максимум четыре» — правило
планирования, а не выдуманный параметр OpenCode.

## DeepSeek V4 Pro в Alibaba

[S5] https://www.alibabacloud.com/help/en/model-studio/deepseek-v4-pro
Страница обновлена 20.09.2026. ID: `deepseek-v4-pro`. В таблице China (Beijing):
текстовый вход/выход, function calling и structured outputs. Указаны context window
1 000 000 и максимальный output 393 216 токенов. Это документированные пределы,
а не проверенные на ключе владельца значения и не рекомендуемый бюджет каждого ответа.

[S6] https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions
Для `deepseek-v4-pro` без суффикса описаны effort `high` и `max`; `low` и `medium`
отображаются в `high`. У `deepseek-v4-pro-0813` описание отличается. Для hybrid thinking
используется `enable_thinking`; `max_completion_tokens` включает reasoning и ответ.
В raw HTTP параметры лежат на верхнем уровне; SDK `extra_body` — не отдельное
вложенное поле wire JSON.

[S7] https://www.alibabacloud.com/help/en/model-studio/deep-thinking
Страница обновлена 22.09.2026. DeepSeek V4 указан как hybrid thinking с включением
по умолчанию. Reasoning и final content разделены. Потоковый пример показывает
usage после finish chunk и перед `[DONE]`.

**Решение пакета:** добавить отдельную пользовательскую модель через существующий
AlibabaProvider, не копировать вслепую профиль V4.1 Flash, не менять endpoint.
Начальная UI-семантика — provider default и подтверждённые OFF/HIGH/MAX; LOW не
выдавать за самостоятельную интенсивность на основании HTTP 200. Подтверждение
доступности, параметров и multi-round tools на конкретном endpoint/credential
остаётся отдельной opt-in проверкой.

## Что взято не из Интернета

Сообщение владельца: существующие модели отвечают, но не всегда; Kimi работает через
OpenCode Desktop; требуются субагенты и добавление DeepSeek V4 Pro.

Первоначальное ТЗ: `.agents/reports/`, максимум четыре независимых субагента,
фиксированные endpoints, owner ID, отсутствие cross-model fallback, скрытый reasoning.

Исторические выводы A01–A40: только `audit_original/`. Они не перепроверялись заново
в этой пересборке и не объявляются автоматически актуальными для более нового checkout.
