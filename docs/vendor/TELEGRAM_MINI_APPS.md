# Telegram Mini Apps — vendor doc

- **Дата проверки:** 2026-09-18
- **Актуальность:** раздел Mini Apps core.telegram.org/bots/webapps, изменения по Bot API 10.1 (2026-06-11)
- **Источники:** core.telegram.org/bots/webapps (изучено главным агентом напрямую, см. сессию M0)

## Что реально использует проект

| Возможность | Использование |
|---|---|
| `<script src="https://telegram.org/js/telegram-web-app.js">` в `<head>` | Подключение JS API |
| `Telegram.WebApp.initData` (RAW) | Единственный credential → backend API |
| Валидация initData (HMAC-SHA-256) | Обязательная серверная проверка каждого логина |
| `auth_date` freshness | Отсев устаревших данных |
| `themeParams` / CSS vars `--tg-theme-*` | Тема UI |
| `WebApp.ready()`, `expand()`, `close()` | Жизненный цикл UI |
| Menu Button (`MenuButtonWebApp`) | Основной вход в Mini App («⚙️ Настройки») |
| Запуск через inline-кнопку `web_app` | Вход в admin-раздел из /admin (owner) |
| `BackButton` | Навигация внутри Mini App |

## Контракт валидации initData (точный алгоритм)

```
data_check_string = все поля кроме hash, отсортированные по key, "k=v", через "\n"
secret_key        = HMAC_SHA256(key="WebAppData", msg=bot_token)
calculated        = HEX(HMAC_SHA256(key=secret_key, msg=data_check_string))
valid             = compare_digest(calculated, hash)
```
Плюс: проверка `auth_date` (свежесть), `user.id`, активного access grant.
`initDataUnsafe` — **не доверять никогда**.

## Замечания по режимам запуска

- Наш Mini App — конфигурационный UI; он общается с backend по HTTPS REST с initData-auth.
- `answerWebAppQuery` / `sendData` **не используются** (нет необходимости слать сообщения от имени пользователя).
- Режим запуска: menu button (все) + inline button из `/admin` (owner).

## Противоречия/пробелы

1. Валидность `query_id` по времени не документирована — нам не нужен (нет answerWebAppQuery).
2. Третьесторонняя валидация по `signature`/Ed25519 (Bot API 8.0+) — не нужна (мы первый-party, у нас есть токен).

## Решение проекта

После валидации initData backend выдаёт короткоживущий server session token (exp ~15 мин);
все admin endpoints дополнительно требуют `user_id == 795063564`. См. DECISIONS.md ADR-007.
