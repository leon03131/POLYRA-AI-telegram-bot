# web5: экран чата Mini App (frontend)

Task: полноценный экран чата `/chats/:id` в стиле мобильных AI-приложений (Qwen)
с SSE-стримингом ответа по контракту `app/api/routes/chat_web.py` (backend —
параллельный агент, `fix-v2/polyra-db-api/web5-chat-web-backend.md`).

Scope: только miniapp/src (pages/components/api/App/styles). Backend, admin/**,
miniapp/e2e не тронуты. Writable-файлы: новые ChatPage/Markdown/SideMenu/sse +
правки App.tsx, hooks.ts, types.ts, styles.css, client.ts (1 строка — см.
«Отклонения от списка файлов»), HomePage.tsx (1 строка — сохранение семантики
существующей ссылки).

## Файлы

Новые:
- `miniapp/src/api/sse.ts` — SSE-клиент POST /api/chats/{id}/messages:
  инкрементальный парсер кадров `event:\ndata:\n\n` (буфер + \r\n-нормализация
  после склейки, безопасная граница чанков), fetch с заголовками как у api()
  (Bearer из session; 401 → ОДИН re-auth + повтор — переиспользован
  reauthenticate() из client.ts), response.body.getReader() +
  TextDecoder({stream:true}) (многобайтный UTF-8 не рвётся на границах чанков).
  Типизированные события meta/delta/error/done/cancelled; ChatStreamResult:
  final | error(userPersisted) | interrupted | aborted.
- `miniapp/src/pages/ChatPage.tsx` — экран чата (маршрут `/chats/:id`).
- `miniapp/src/components/Markdown.tsx` — react-markdown + remark-gfm +
  remark-math + rehype-katex (импорт katex/dist/katex.min.css), memo.
- `miniapp/src/components/SideMenu.tsx` — drawer 85% (кап 400px на desktop):
  список чатов (useChats) + «Новый чат» (POST /api/chats → переход), текущий
  подсвечен, закрытие по тапу на затемнение rgba(0,0,0,.6).

Правки:
- `miniapp/src/App.tsx` — маршруты: `/chats/:id` → ChatPage (новый),
  `/chats/:id/settings` → ChatSettingsPage (перенос существующей страницы);
  TabBar скрыт только на `/chats/:id` (иммерсивный чат); BackButtonManager
  и остальные маршруты не тронуты.
- `miniapp/src/pages/HomePage.tsx` — единственная существующая ссылка на
  настройки чата переведена на `/chats/${id}/settings` (семантика «открыть
  настройки» сохранена).
- `miniapp/src/api/hooks.ts` — `qk.messages(chatId)`, `MESSAGES_PAGE_SIZE=50`,
  `useChatMessages(chatId)`: infinite-пагинация НАЗАД; pageParam −1 = tail-режим
  (probe offset=0 → total; короткий чат закрывается 1 запросом, длинный —
  вторым запросом последней страницы offset=max(0,total−50); гонки защищены
  перечитыванием). staleTime 3с — сообщения из Telegram видны при открытии.
- `miniapp/src/api/types.ts` — WebMessage, WebMessagesResponse.
- `miniapp/src/api/client.ts` — `export` у reauthenticate() (иначе SSE-клиент
  не может переиспользовать 401-retry; поведение api() не изменилось).
- `miniapp/src/styles.css` — секция `/* === web5 chat === */` в конце:
  фон #000, бар 72px, title 17px semibold ellipsis, пузырь #202023 radius 20
  max-width 78%, инпут 72px radius 36 #202023 + safe-area, «● ● ●», каретка,
  пейдлы, плашка ошибки, тост, drawer, markdown/кода/таблиц/KaTeX. Существующие
  токены/классы не перезаписаны (всё под префиксом web5-).
- `miniapp/package.json` + lock — deps: katex@0.16.47 (выровнен с nested
  rehype-katex, иначе CSS 0.18 ≠ рендер 0.16), react-markdown@10.1.0,
  remark-gfm@4, remark-math@6, rehype-katex@7.

## Поток (отправка → дельты → done)

1. Enter/кнопка → optimistic user-пузырь (снапшот последних user/assistant id
   из истории) + `POST /api/chats/{id}/messages {"text"}`.
2. `event: meta` — игнорируется UI (модель видна в настройках чата).
3. `event: delta` — текст конкатенируется; до первой дельты «● ● ●»; markdown
   перерендер; автоскролл вниз, только если юзер ≤80px от низа; кнопка
   отправки → Stop (квадрат).
4. Stop → `POST /api/chats/{id}/stop` → SSE-`cancelled` → пейдл
   «Ответ прерван» под partial.
5. `event: done` → invalidate qk.messages + qk.chats → refetch; merge
   optimistic-хвоста с историей: по assistantId (done/cancelled) или по
   «новому» user-сообщению (id ≠ снапшота, text совпадает, created_at ≥
   отправки − 15с) — покрытые gen-ы удаляются, лента бесшовно переходит на
   серверные данные.
6. `event: error` → плашка в ленте (partial сохраняется), user-пузырь скрывается
   после покрытия историей; HTTP-ошибка ДО стрима → текст возвращается в поле,
   optimistic-пузырь скрыт (hideUser).
7. Обрыв (нет error/done/cancelled) → тост «Соединение прервано» + invalidate
   (дозагрузка); генерация на сервере продолжает жить (контракт web5): когда
   она персистится, новый assistant-message (id ≠ снапшота) заменяет partial.
8. Unmount/смена чата через drawer → abort стрима + сброс optimistic + invalidate
   истории прошлого чата (ответ достанется при возврате).

## Решения/инварианты

- Чаты: GET истории — ASC, изначально последняя страница, «Загрузить ещё»
  (fetchPreviousPage, prepend) с компенсацией scrollTop (вьюпорт не прыгает);
  дедуп по id при refetch-перекрытиях страниц.
- Enter=отправить (с guard isComposing для IME), Shift+Enter=перенос; textarea
  авто-высота до ~110px (5 строк); disabled при генерации; safe-area-inset-bottom.
- Иконки — inline SVG stroke 1.6-1.8, без библиотек; SF Pro-стек на web5-классах.
- Настройки чата: существующая страница ChatSettingsPage доступна по
  `/chats/:id/settings` (иконка-слайдеры в баре ведёт туда).
- `getNextPageParam: () => undefined` в useChatMessages: пагинация только назад
  (обязателен в react-query 5.103 — иначе TS-ошибка).
- TabBar скрыт на `/chats/:id` (regex `^/chats/[^/]+$`), на settings-роуте
  таббар остаётся — «существующие маршруты не сломаны».

## Гейты (реальные прогоны, miniapp/)

- `npm run typecheck` → **exit 0** (tsc --noEmit, strict).
- `npm run build` → **exit 0** (tsc && vite build, 398 модулей; KaTeX-шрифты
  сбандлены; предупреждение про чанк 728KB — katex+react-markdown, не гейт).
- Smoke-тест парсера SSE (логика 1:1 из sse.ts, node): 8/8 PASS — покадровая
  разбивка, границы чанков посимвольно, \r\n между чанками, юникод, кадры без
  data, дефолтный event. exit 0.
- TextDecoder({stream:true}) посимвольная сборка «Привет 👋 мир» → PASS, exit 0.

## Отклонения от списка файлов (сознательные, минимальные)

- `client.ts`: `export async function reauthenticate()` — иначе sse.ts дублирует
  401-reauth логику или теряет retry. Поведение api() не изменено.
- `HomePage.tsx`: ссылка «настройки чата» → `/chats/${id}/settings` (без этого
  ссылка вела бы на экран чата — изменилась бы семантика существующего UI).
- Drawer: max-width 400px сверх 85% ширины (85% на десктопе — на весь экран).

## Blockers

Нет. Для живой E2E-проверки SSE нужен backend с generation_service + БД
(не входило в задачу; API-контракт сверен с chat_web.py построчно).
