# Официальные внешние источники

Прочитаны 24 сентября 2026. Это источники протокольных правил; конкретные ошибки проекта устанавливаются локальным кодом и диагностикой, не этими страницами. Примеры endpoints в документации Alibaba не являются разрешением менять заданный владельцем endpoint.

| Источник | Адрес | Использование |
|---|---|---|
| Telegram Bot API | `https://core.telegram.org/bots/api` | Rich Message Draft/Stop, HTML parse_mode, ограничения длины, правила финализации |
| Telegram Mini Apps | `https://core.telegram.org/bots/webapps` | Server-side validation raw initData и недоверие initDataUnsafe |
| Alibaba Model Studio: Streaming output | `https://www.alibabacloud.com/help/en/model-studio/stream` | Последний chunk с usage может приходить после finish_reason и до [DONE]; документ обновлён 18.09.2026 |
| Alibaba Model Studio: Kimi K3 | `https://www.alibabacloud.com/help/en/model-studio/kimi-k3` | Модель/возможности существуют в актуальной документации; нельзя объявлять их вымышленными по устаревшим знаниям |
| Google Gemini: Thought signatures | `https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures` | Сохранение служебных подписей function-call контекста; в текущем Gemini adapter уже реализовано |
| OWASP SSRF Prevention Cheat Sheet | `https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html` | DNS rebinding, проверка сетевых адресов и defense-in-depth для A19 |
| Публичная страница проекта | `https://github.com/leon03131/POLYRA-AI-telegram-bot` | Контекст репозитория; не установлено равенство скачанному ZIP конкретного HEAD SHA |

Источники подтверждают семантику APIs, но не доказывают, что конкретный ключ владельца имеет доступ ко всем моделям/режимам. Это устанавливается только контролируемыми live probes. Сохранённые в ZIP отчёты сентября 2026 — исторические свидетельства, не новая live-проверка данного аудита.
