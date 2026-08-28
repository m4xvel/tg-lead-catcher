# Границы

Проектные правила, которые субагент не может вывести сам:

- **Стек:** Python 3.11+, Telethon (userbot), aiogram 3 (панель), SQLite через `aiosqlite`.
- **Запуск:** `docker compose up` (сервисы `userbot`, `panel`) или локально
  `python -m userbot.main` / `python -m panel.main`. Тесты — `pytest`.
- **Не трогать без причины:** `.gitignore`, `.env.example` (только дописывать
  новые имена секретов, без значений), `.autopilot/`.
- **Секреты:** только имена переменных в `.env.example`. Если для тикета не
  хватает переменной — добавь имя с пустым значением в `.env.example`, не
  спрашивай значение и не выдумывай его.
- **Отсутствующая зависимость** (пакет, которого нет и до которого не дотянуться)
  — тикет возвращается `BLOCKED` с точным именем зависимости, не устанавливается
  через обход политики и не имитируется заглушкой, выдаваемой за рабочую.
- **Язык:** весь пользовательский текст (сообщения бота, ошибки) — на русском.

## Границы, решённые в спецификации

| Модуль | Владеет | Выставляет | Прячет |
|---|---|---|---|
| `matcher` | компиляцию ключей, правило совпадения | `compile(keywords, stopwords) -> Ruleset`; `Ruleset.match(text) -> MatchResult{matched, hit_keywords, blocked_by}` | regex-кэш, нормализацию регистра/ё-е, границы слов |
| `store` | схему SQLite, все таблицы (`sources`, `receivers`, `keywords`, `settings`, `hits`, `delivery_queue`) | по репозиторию на сущность: `sources`, `receivers`, `keywords`, `hits`, `delivery_queue`, `settings` — типовые `add/remove/list/get/update` на каждый | SQL, миграции, соединение с БД |
| `userbot` | сессию Telethon, обработку входящих/правок, исполнение доставки | `list_dialogs()`, `catch_up(chat_id, limit_days, limit_msgs)`, `deliver(hit, receiver) -> DeliveryResult` | Telethon-клиент, форвард/копия как деталь `deliver` |
| `panel` | UI-состояние (FSM), тексты и клавиатуры | ничего наружу — конечная точка; вызывает `store` и `userbot.list_dialogs/catch_up` | роутеры и клавиатуры aiogram |
| `worker` | цикл очереди, глобальный рейт-лимит, ретраи | `run()` (фоновый цикл) | таймеры бэкоффа, счётчик отправок в минуту |

## Швы для тестов

- `matcher.Ruleset.match` — весь матчинг (слово/фраза/regex/регистр/ё-е/минус-слова)
  проверяется здесь, без Telegram и без БД.
- `store`-репозитории — дедуп по id и по автору+тексту, ретеншн, очередь доставки —
  проверяются на реальной (временной, `:memory:` или tmp-файл) SQLite, без сети.
- `userbot.deliver` — форвард/копия/карточка проверяется с фейковым Telethon-клиентом
  за тем же интерфейсом (без реального аккаунта).

Предпочитай эти три шва любым новым. Три, не один — `matcher` и `store` живут без
Telegram вовсе, гонять их через сеть было бы медленно и хрупко.

## Схема данных (SQLite, `./data/bot.db`)

```
sources(id, chat_id UNIQUE, title, kind[group|supergroup|channel|private],
        paused BOOL, last_processed_msg_id, added_at)
receivers(id, chat_id UNIQUE, title, added_at)
keywords(id, kind[word|phrase|regex], pattern, is_stop BOOL, created_at)
settings(key PRIMARY KEY, value)   -- tz (default "Europe/Moscow"), dedup_window_days,
                                    -- dedup_enabled, rate_limit_per_min, scan_days,
                                    -- scan_msgs, retention_days, card_template,
                                    -- monitoring_paused, monitor_all_dm
hits(id, source_chat_id, message_id, author_id, author_username, author_name,
     matched_keywords, text_hash, text_preview[500], also_in, created_at)
delivery_queue(id, hit_id, receiver_chat_id, status[pending|sent|failed],
                attempts, next_attempt_at, error)
```

Уникальный индекс `(source_chat_id, message_id)` на `hits` — дедуп по id (R32).
Индекс `(author_id, text_hash, created_at)` — дедуп по автору+тексту за окно (R33).

## Прогресс по тикетам

### Из тикета 01 — ядро (matcher, store)

- `matcher.compile(keywords, stopwords) -> Ruleset` — `keywords`/`stopwords`:
  список кортежей `(kind, pattern)`, `kind ∈ {word, phrase, regex}`. Разбор
  сырого ввода (`re:` префикс, кавычки для фразы) — забота панели/store, не
  `matcher`.
- `Ruleset.match(text) -> MatchResult(matched, hit_keywords, blocked_by)`
- `matcher.InvalidKeywordError` — на кривой `regex` при `compile`
- `store.connect(path) -> aiosqlite.Connection`; `store.create_schema(conn)`
- Репозитории: `store.{SourcesRepo, ReceiversRepo, KeywordsRepo, SettingsRepo,
  HitsRepo, DeliveryQueueRepo}`, каждый — `add/remove/list/get/update` под свою
  сущность
- `HitsRepo.is_duplicate_by_author_text(author_id, text_hash, window_days,
  dedup_enabled=True, now=None) -> bool`
- `store.DuplicateHitError`, `store.DuplicateChatError`
- Тесты: `python -m pytest`, один файл — `python -m pytest <path>`
- **Известное ограничение (см. D01 в манифесте):** граница слова реализована
  как `\b<ключ>\w*` — ловит любое слово, начинающееся с ключа. Пример из
  брифа «ремонт» → «ремонтник» этим покрывается; пример «авто» не должно
  ловить «автора» — технически не выполним тем же правилом без
  морфологического анализа, который R72 явно исключает. Осознанное решение,
  не забытый баг — не переоткрывать в следующих тикетах.
