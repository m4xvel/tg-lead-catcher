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

### Из тикета 02 — userbot: приём, матчинг, догон

- **Канал уведомлений владельцу (R43/R64/R76i) — обязателен для тикета 03:**
  `userbot.notify_owner(client, text: str) -> None` (`userbot/client.py`) —
  шлёт `text` в «Избранное» того же аккаунта (`client.send_message("me", text)`).
  Тикет 03 обязан звать эту же функцию для уведомлений о недоступных
  приёмниках/сбоях доставки — не заводить второй канал (BOT_TOKEN здесь не
  участвует).
- `userbot.build_client(session_path=None) -> TelegramClient` — сессия по
  умолчанию `./data/userbot.session` (R66), API_ID/API_HASH из окружения.
  Только конструирует клиент, не логинит и не подключает — это уже
  `setup.py`/точка входа.
- `userbot.IncomingMessage(chat_id, message_id, text, is_own, sender_is_bot,
  author_id, author_username, author_name)` — нормализованный вход пайплайна,
  общий для live-событий и догона.
- `userbot.Deps(sources, receivers, hits, delivery_queue, settings,
  ruleset_provider)` — контейнер зависимостей обработчиков;
  `ruleset_provider` — `() -> matcher.Ruleset`, чтобы правило матчинга
  пересчитывалось на актуальные ключи, а не фиксировалось при старте.
- `userbot.process(msg: IncomingMessage, *, sources, receivers, hits,
  delivery_queue, settings, ruleset) -> store.Hit | None` — пайплайн §4
  (фильтры → матчинг → дедуп → запись `hits`/`delivery_queue` → сдвиг
  `last_processed_msg_id`). Обновляет `last_processed_msg_id` только при
  новом срабатывании (буквально по шагам спеки) — не на каждое сообщение.
- `userbot.register_handlers(client, deps: Deps) -> None` — вешает
  `NewMessage`/`MessageEdited`/`Album` хендлеры на Telethon-клиент (реальный
  или фейковый с тем же интерфейсом `add_event_handler`). Альбом — одна запись
  `hits` на весь пост (id — минимальный `message_id` группы), не по фото.
- `userbot.catch_up(client, chat_id, since_id, *, limit_days=None,
  limit_msgs=None, pause_seconds=1.0, process=None) -> int | None` —
  низкоуровневый догон одного чата; `limit_days`/`limit_msgs=None` — без
  предохранителя (используется на старте, R35/R54).
- `userbot.catchup.scan_source(client, source, *, sources, receivers, hits,
  delivery_queue, settings, ruleset_provider, limit_days=None,
  limit_msgs=None, pause_seconds=1.0) -> int | None` — скан с предохранителем
  (R37): без явных лимитов берёт `settings.scan_days`/`scan_msgs`, но
  `limit_msgs` всегда зажат `HARD_MSG_CAP=500`. Этим тикет 04 дёргает и
  кнопку «Просканировать N дней» (передав `limit_days` явно, до 7), и снятие
  индивидуальной паузы источника (R78i — без доп. параметров, без диалога о
  догоне; предварительно тикет 04 сам снимает `paused` через
  `store.SourcesRepo.update`).
- `userbot.run_startup_catchup(client, *, sources, receivers, hits,
  delivery_queue, settings, ruleset_provider, notify=None,
  pause_seconds=1.0) -> dict[chat_id, last_id|None]` — вызывается один раз при
  старте процесса `userbot`; источник, упавший на недоступности (кикнули, чат
  удалён — `userbot.catchup.INACCESSIBLE_SOURCE_ERRORS`), помечается
  `paused=True` и уведомляет ровно один раз через `notify_owner` (R76i,
  симметрично R43 из тикета 03).
- `userbot.list_dialogs(client, *, offset=0, limit=8, query=None) ->
  list[DialogInfo]` (`DialogInfo(chat_id, title, kind)`) — только
  группы/супергруппы/каналы (без личных чатов), одна страница за вызов, не
  грузит все диалоги в память (R38).
- Тесты: `python -m pytest tests/userbot` (без `test_deliver.py` — зона
  тикета 03), фейковый Telethon-клиент — `tests/userbot/fakes.py`
  (`FakeTelegramClient`, `FakeEvent`, `FakeAlbumEvent`, `FakeSender`).
- **Осознанное решение:** `userbot/main.py` (точка входа `python -m
  userbot.main`, которую уже ждёт `docker-compose.yml` из тикета 06) — вне
  зоны тикета 02 (её нет в списке файлов тикета) и не реализован здесь;
  вероятно, ложится на тикет 03 вместе с циклом `worker`, который по спеке
  §1 живёт в том же процессе `userbot`.

### Из тикета 06 — мастер настройки, автозапуск

- `setup.py`: `login_userbot(client, phone, ask_code, ask_password, say,
  max_attempts=3) -> User` (бросает `LoginAborted` при исчерпании попыток);
  `verify_liveness(userbot_client, bot_client, owner_id) -> LivenessResult`;
  `format_liveness(result) -> str`; `parse_env_text(text) -> dict`,
  `render_env_text(values) -> str`, `write_env(path, values)`. Всё это —
  внутренние функции самого мастера, наружу другим тикетам не нужны.
- `docker-compose.yml`/`Dockerfile` ожидают точки входа `python -m userbot.main`
  и `python -m panel.main` — их создают тикеты 02/03 и 04/05 соответственно;
  если у них другие модульные пути, поправить `docker-compose.yml`.
- Тесты: `python -m pytest tests/test_setup.py`

### Из тикета 03 — доставка и устойчивость (worker, userbot.deliver)

Канал уведомлений владельцу уже был задан тикетом 02 к моменту, когда он мне
понадобился (`userbot/client.py`) — использован дословно, второй канал не
заводился:

- `userbot.client.notify_owner(client, text: str) -> None` — шлёт `text`
  Telethon-клиентом в `"me"` (Избранное того же аккаунта).

Фактические сигнатуры (уточняют набросок в «Границы, решённые в спецификации» —
там `deliver(hit, receiver)` без клиента/источника, этого не хватает для форварда
и для ссылки на оригинал):

- `userbot.deliver.deliver(client, hit: store.Hit, source: store.Source,
  receiver: store.Receiver, *, card_template: str = DEFAULT_CARD_TEMPLATE,
  tz: str = "Europe/Moscow") -> DeliveryResult{ok, used_copy_fallback}` —
  форвард с фолбэком на копию (`ChatForwardsRestrictedError`) + карточка
  вторым сообщением. Исключения `FloodWaitError`/`AuthKeyUnregisteredError`/
  `SessionRevokedError`/прочие сюда не перехватываются — всплывают вызывающему
  (`worker`), ретраи/бэкофф/уведомления — не забота `deliver`.
- `userbot.deliver.build_card(template, *, hit, source, link, tz) -> str` —
  рендер карточки отдельно от отправки (пригодится `panel` для предпросмотра
  шаблона, R31).
- `client` — Telethon `TelegramClient` или дублирующий его интерфейс фейк:
  `forward_messages(to_chat_id, message_id, from_peer)`, `get_messages(chat_id,
  ids)`, `send_message(chat_id, text)`, `get_entity(chat_id)` (последний — для
  username при построении ссылки на оригинал).
- `worker.process_pending(conn, client, *, limiter: RateLimiter, now=None,
  sleep=asyncio.sleep) -> Cycle{session_dead}` — один проход `delivery_queue`;
  `worker.run(conn, client, *, poll_interval=5.0, sleep=asyncio.sleep) -> None`
  — бесконечный цикл поверх него, останавливается сам при смерти сессии.
  Читает `settings.rate_limit_per_min/card_template/tz` заново на каждый круг —
  правка в UI подхватывается без перезапуска.
- `worker.RateLimiter(limit_per_min)` — скользящее окно 60с **в памяти
  процесса** (не в БД: `delivery_queue` не хранит время отправки, а `worker`
  живёт одним процессом внутри `userbot`).
- `worker.backoff_seconds(attempts) -> int` — 30с/120с/480с.
- `worker.purge_expired(conn, retention_days, *, now=None) -> int` (R68) —
  прямой SQL по `conn`, а не через `HitsRepo`: ни `HitsRepo`, ни
  `DeliveryQueueRepo` не выставляют массовое удаление по возрасту, только
  `remove(id)`. Осознанное отступление от «SQL только внутри store» — see
  CONCERNS в отчёте тикета.
- `python -m userbot.main` / `worker.run` внутри него — не создавал: точки
  входа отдельным тикетом 07 (`07-tochki-vhoda.md`), не в зоне 02/03.
- Тесты: `python -m pytest tests/userbot/test_deliver.py tests/worker/`;
  фейковый Telethon-клиент — `tests/conftest.py` (`fake_client`, `conn`).

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

### Из тикета 05 — панель: ключи, статистика, экспорт

- `panel.keywords.build_router()`, `panel.stats.build_router()`,
  `panel.export.build_router()` — свежий `Router` на каждый вызов (тот же
  паттерн, что `panel.sources.build_router()`/`panel.receivers.build_router()`
  в тикете 04). Ни один не оборачивает себя `OwnerAccessMiddleware` сам —
  тикет 07 подключает их так же, как `panel/__init__.py` подключает
  sources/receivers (либо через общий `panel.build_router()`, если его
  дополнят, либо оборачивая каждый отдельно этим middleware при прямом
  `Dispatcher.include_router()`).
- `panel.keywords.parse_keyword_line(line) -> (kind, pattern) | None`,
  `add_keyword_lines(text, keywords_repo, *, is_stop) -> AddResult{added,
  errors}` — построчный разбор `re:`/`"фраза"`/слова и молчаливый дедуп
  (регистр/ё-е нормализуются для word/phrase, regex сравнивается как есть).
  `build_ruleset(keywords_repo) -> matcher.Ruleset` и
  `format_check_result(matcher.MatchResult) -> str` — три точных текста
  брифа для «🧪 Проверить» (R47).
- `panel.stats.compute_period_stats(hits, days, now=None) -> PeriodStats{total,
  by_keyword: Counter, by_chat: Counter}`, `top_keywords(stats, limit=10)`,
  `format_status/format_stats/format_top_keywords/format_history`,
  `build_message_link(source_chat_id, message_id) -> str` (форма
  `t.me/c/<internal_id>/<id>` — работает для владельца независимо от
  публичности чата, без обращения к Telethon).
- **Важно для тикета 07 — R53 и порядок роутеров:** `panel.stats.py` содержит
  собственный `on_toggle_monitoring`, зарегистрированный на тот же текст кнопок
  `kb.BTN_PAUSE_ON`/`BTN_PAUSE_OFF`, что и `on_toggle_monitoring` в
  `panel/sources.py` (тикет 04) — тот реализует только R48 (мгновенный тумблер),
  без вопроса о догоне из R53. `panel/sources.py` вне зоны тикета 05 и не
  тронут. Чтобы диалог R53 реально появлялся, роутер `panel.stats.build_router()`
  должен быть подключён к диспетчеру **раньше** `panel.build_router()` (или
  раньше его under-роутера `sources`) — aiogram отдаёт апдейт первому
  совпавшему хендлеру, дальше не идёт. Если тикет 07 подключит в порядке
  «сначала 04, потом 05» — R53 не сработает, будет срабатывать только
  мгновенный тумблер тикета 04. Новая настройка `settings.monitoring_paused_at`
  (не в `DEFAULT_SETTINGS`, пишется этим хендлером при постановке на паузу) —
  используется для расчёта длительности простоя.
- `panel.export.build_export_payload(*, sources, keywords, receivers) -> dict`
  с ключами `sources/keywords/stopwords/receivers`; `cmd_export` отправляет их
  файлом (`BufferedInputFile`, `SendDocument`) по `/export` — не сообщением с
  JSON-текстом.
- `panel/keyboards.py` дополнен (существующее для источников/приёмников не
  тронуто): `TARGET_KEYWORD`, `TARGET_STOPWORD`, кнопки `BTN_KEYWORDS/
  BTN_STOPWORDS/BTN_STATUS/BTN_STATS/BTN_TOP/BTN_HISTORY/BTN_CHECK`,
  `CheckStartCB`, `ResumeConfirmCB{answer}`, `StatsPeriodCB{days}`,
  `keyword_list_kb`, `resume_confirm_kb`, `stats_period_kb`; `main_menu_kb`
  получил дополнительные ряды кнопок (сама функция и её прежние ряды не
  менялись, только дописаны новые).
- Тесты: `python -m pytest tests/panel/test_keywords.py tests/panel/test_stats.py
  tests/panel/test_export.py` — через `Dispatcher.feed_update` со сфабрикованными
  `Update`, как тесты тикета 04; `tests/panel/conftest.py` дополнен поддержкой
  `SendDocument` в `FakeSession` (существующие ветки не менялись).
