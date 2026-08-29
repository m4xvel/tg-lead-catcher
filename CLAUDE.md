<!-- autopilot:start -->
# tg-lead-catcher

Telegram-решение для лидогенерации: два процесса (userbot на Telethon, panel на aiogram)
ловят сообщения по ключевым словам в выбранных чатах и пересылают их в целевой чат;
настройка источников/приёмников/ключей — через UI бота-панели.

## Команды

Установка (dev): `pip install -e ".[dev]"`

Первичная настройка (один раз, руками — создаёт `.env`, логинит обе Telethon-сессии): `python configure.py`

Запуск (Docker, оба сервиса, автоперезапуск): `docker compose up -d`

Запуск локально (после `python configure.py`): `python -m userbot.main` и отдельно `python -m panel.main`

Автозапуск без Docker (Windows, задача Планировщика): см. `deploy/windows-task.md`, скрипт `deploy/start-local.ps1`

Тесты, полный набор (из корня): `python -m pytest` — 170 passed на момент последней сборки

Тесты, один файл: `python -m pytest tests/matcher/test_ruleset.py -q` — проверено, 9 passed

## Структура

- `matcher/` — компиляция ключевых слов в правило совпадения, без Telegram и БД
- `store/` — схема SQLite и репозитории по сущности (`sources`, `receivers`, `keywords`, `hits`, `delivery_queue`, `settings`)
- `userbot/` — Telethon-сессия, приём живых сообщений, догон истории, отправка (`deliver`)
- `worker/` — фоновый цикл доставки очереди, рейт-лимит, ретраи, ретеншн; живёт внутри процесса `userbot`, не отдельный сервис
- `panel/` — aiogram-бот: меню, FSM-мастера ввода источников/приёмников/ключей, статистика, экспорт
- `tests/` — зеркалит структуру пакетов (`tests/matcher`, `tests/store`, `tests/userbot`, `tests/worker`, `tests/panel`, `tests/test_configure.py`)
- `deploy/` — автозапуск без Docker на Windows (Планировщик задач)
- `configure.py` — отдельная точка входа, мастер первой настройки (было `setup.py` — переименован: имя конфликтовало с setuptools как legacy build-скрипт); не импортируется рантаймом и не входит в Docker-образ
- `docker-compose.yml` / `Dockerfile` — общий образ, две команды (`userbot.main`, `panel.main`), общий volume `./data`

## Ключевые файлы

- `userbot/main.py` — точка входа `python -m userbot.main`: поднимает клиент, стартовый догон, live-хендлеры, фоновые задачи (`worker.run`, `worker.retention.run_daily`, обновление `Ruleset` раз в 30с)
- `panel/main.py` — точка входа `python -m panel.main`: свой Telethon-клиент (`./data/panel.session`), свой Bot API клиент, один роутер `panel.build_router(owner_id)`
- `panel/__init__.py` — `build_router(owner_id) -> Router`, единственная точка сборки панели; здесь же критичный порядок `include_router` (stats перед sources)
- `userbot/client.py` — `build_client(session_path=None)`, `notify_owner(client, text)` (единственный канал уведомлений владельцу — сообщение в «Избранное»)
- `userbot/deliver.py` — `deliver(client, hit, source, receiver, *, card_template=..., tz=...)`, `build_card(template, *, hit, source, link, tz)`
- `worker/runner.py` — `process_pending(conn, client, *, limiter, now=None, sleep=...)`, `run(conn, client, *, poll_interval=5.0, sleep=...)`, `backoff_seconds(attempts)` (30/120/480с)
- `matcher/ruleset.py` — `compile(keywords, stopwords) -> Ruleset`, `Ruleset.match(text) -> MatchResult`
- `store/db.py`, `store/repositories/*.py` — схема и репозитории; `store/__init__.py` реэкспортирует всё публичное

## Архитектура

Два независимых процесса, разделяющих `./data/bot.db` (SQLite) и volume `./data`:
- **userbot** (`userbot/main.py`) — читает Telegram живьём (Telethon `NewMessage`/`MessageEdited`/`Album`), матчит, пишет `hits`/`delivery_queue`, доставляет (фоновый `worker.run` внутри того же процесса), догоняет пропущенное при старте и по запросу панели.
- **panel** (`panel/main.py`) — только управление: aiogram-бот на `BOT_TOKEN`, читает/пишет `store` напрямую, у списков диалогов и ручного скана дёргает `userbot.list_dialogs`/`userbot.catchup.scan_source` через свой собственный Telethon-клиент (не тот же объект, что у процесса userbot — процессы не делят клиент, только БД и файловую систему `./data`).

Поток данных от сообщения до доставки: Telethon-событие → `userbot.IncomingMessage` (нормализация) → `userbot.process(msg, *, sources, receivers, hits, delivery_queue, settings, ruleset)` → фильтры (пауза источника, не-бот, не-свой) → `Ruleset.match` → дедуп по id и по автору+тексту за окно (`HitsRepo.is_duplicate_by_author_text`) → запись `hits` + постановка в `delivery_queue` (status=pending) → `worker.run` циклом читает `delivery_queue`, соблюдает глобальный рейт-лимит (`RateLimiter`, скользящее окно 60с в памяти процесса), вызывает `userbot.deliver.deliver` (форвард с фолбэком на копию при `ChatForwardsRestrictedError` + карточка вторым сообщением) → при провале — ретрай с бэкоффом (30/120/480с, до 3 попыток) → при смерти сессии (`AuthKeyUnregisteredError`/`SessionRevokedError`) уведомление уходит резервным каналом через Bot API (`worker/bot_notify.py`), не через саму умершую MTProto-сессию.

Границы модулей (кто чем владеет, что выставляет наружу):
- `matcher` — владеет regex-кэшем, нормализацией регистра и ё→е; выставляет только `compile`/`Ruleset.match`/`InvalidKeywordError`.
- `store` — владеет схемой SQLite и SQL целиком; выставляет по репозиторию на сущность (`SourcesRepo`, `ReceiversRepo`, `KeywordsRepo`, `HitsRepo`, `DeliveryQueueRepo`, `SettingsRepo`), каждый — `add/remove/list/get/update` под свою сущность. Единственное отступление — `worker/retention.py` бьёт по `conn` напрямую (массовое удаление по возрасту репозитории не выставляют).
- `userbot` — владеет Telethon-сессией и деталями форварда/копии; выставляет `build_client`, `notify_owner`, `list_dialogs`, `catch_up`/`catchup.scan_source`, `run_startup_catchup`, `register_handlers`, `process`, `deliver`, `build_card`, `Deps`, `IncomingMessage`.
- `worker` — владеет циклом очереди, рейт-лимитом, бэкоффом; выставляет `run`, `process_pending`, `RateLimiter`, `backoff_seconds`, `purge_expired`, `notify_owner_via_bot_api`.
- `panel` — владеет FSM-состоянием, текстами и клавиатурами; наружу не выставляет ничего (конечная точка), сама вызывает `store` и `userbot.list_dialogs`/`userbot.catchup.scan_source`.

## Соглашения кода

- Все репозитории `store` дают одинаковый набор методов на сущность: `add/remove/list/get/update` — новый функционал в `store` следует этому шаблону, а не произвольному SQL за пределами `store/repositories/*.py` (кроме уже задокументированного отступления в `worker/retention.py`).
- Три швa для тестов, предпочитай их новым: `matcher.Ruleset.match` (без Telegram и БД), `store`-репозитории на временной SQLite (`:memory:` или tmp-файл, без сети), `userbot.deliver`/панель — с фейковым Telethon-клиентом за тем же интерфейсом (`FakeTelegramClient` и т.п. в `tests/userbot/fakes.py`, `tests/conftest.py`, `tests/panel/conftest.py`).
- `build_router()` — каждый модуль панели (`sources`, `receivers`, `keywords`, `stats`, `export`) отдаёт свежий `aiogram.Router` на каждый вызов; доступ владельца (`OwnerAccessMiddleware`) вешается один раз в `panel.build_router(owner_id)`, под-роутеры сами доступ не проверяют.
- Фоновые asyncio-задачи создаются через `_spawn_tracked_task` (`userbot/main.py`) с сильной ссылкой в `background_tasks`, никогда голым `asyncio.create_task(...)` без сохранённой ссылки — иначе GC может собрать задачу на середине выполнения.
- Весь пользовательский текст (сообщения бота, ошибки, уведомления владельцу) — на русском.
- Тестируемая логика вынесена в чистые функции / функции с инъекцией зависимостей (`ask`/`say` в `configure.py`, `now`/`sleep` в `worker`, `client`/`tg_client` в точках входа) — интерактивные/блокирующие обёртки (`main()`, `run_until_disconnected()`, `start_polling()`) тонкие и вручную не тестируются.

## Окружение

Переменные `.env` (см. `.env.example`, только имена — без значений):

- `API_ID`, `API_HASH` — доступ к Telegram API (my.telegram.org), нужны для обеих Telethon-сессий (userbot и panel)
- `BOT_TOKEN` — токен бота панели (@BotFather), Bot API для aiogram и для резервных уведомлений при смерти MTProto-сессии
- `OWNER_ID` — числовой Telegram ID, единственный, кому панель отвечает (`OwnerAccessMiddleware`)
- `TZ` — часовой пояс для отметок времени в карточках (дефолт Europe/Moscow, если пусто)

## Тесты

Фреймворк — pytest + pytest-asyncio (`asyncio_mode = "auto"`, см. `pyproject.toml`). Структура зеркалит пакеты:

- `tests/matcher/test_ruleset.py` — весь матчинг (слово/фраза/regex/регистр/ё-е/минус-слова), без Telegram и БД
- `tests/store/` — репозитории на временной SQLite
- `tests/userbot/` — `fakes.py` даёт `FakeTelegramClient`/`FakeEvent`/`FakeAlbumEvent`/`FakeSender`; `test_deliver.py` использует фейки из `tests/conftest.py`
- `tests/worker/` — рейт-лимит, ретеншн, цикл `run`/`process_pending`
- `tests/panel/` — `conftest.py` даёт `FakeSession` (aiogram Bot API без сети) и фейковый Telethon-клиент; хендлеры гоняются через `Dispatcher.feed_update`
- `tests/test_configure.py` — парсинг/запись `.env`, разбор ошибок логина, с фейковым Telethon-клиентом

Запуск одного файла: `python -m pytest tests/matcher/test_ruleset.py -q`
Запуск одного модуля тестов: `python -m pytest tests/worker/`

## Подводные камни

- userbot и panel используют РАЗНЫЕ Telethon-сессии — `./data/userbot.session` и `./data/panel.session` (см. `userbot/client.py:DATA_DIR/SESSION_NAME` и `panel/main.py:PANEL_SESSION_NAME`) — общий файл сессии между двумя процессами ловит гонку/повреждение. Обе логинятся одним номером телефона через `python configure.py` (`login_both_sessions`), это два независимых входа в один аккаунт (как Desktop и Web одновременно), не два аккаунта.
- Ключи типа `regex` НЕ проходят ё→е-нормализацию, только `re.IGNORECASE` — намеренно (`matcher/ruleset.py`): нормализация трансформировала бы сам паттерн (например `[A-Z]` → `[a-z]`), ломая синтаксис или инвертируя смысл. Слово/фраза нормализуются, regex — нет.
- Фоновые задачи в `userbot/main.py` (`refresh_loop`, `worker.run`, `retention.run_daily`) держат ссылку через `background_tasks` (сет в кадре `run()`), не голый `asyncio.create_task(...)` — без сильной ссылки GC может собрать задачу без ошибки и без следа.
- `panel/sources.py` и `panel/stats.py` оба регистрируют `on_toggle_monitoring` на одну и ту же кнопку паузы — aiogram отдаёт апдейт первому совпавшему хендлеру. Порядок `include_router` в `panel/__init__.py` критичен: `stats` должен идти раньше `sources`, иначе диалог о догоне (R53) не показывается, срабатывает только мгновенный тумблер из `sources`.
- Уведомления владельцу идут по одной MTProto-сессии (`userbot.notify_owner`, сообщение в «Избранное»), кроме одного случая — смерти самой этой сессии (`AuthKeyUnregisteredError`/`SessionRevokedError`): тогда используется резервный канал через Bot API напрямую (`worker/bot_notify.py:notify_owner_via_bot_api`), без aiogram.
- `worker/retention.py` бьёт SQL по `conn` напрямую в обход репозиториев — осознанное отступление, потому что ни `HitsRepo`, ни `DeliveryQueueRepo` не выставляют массовое удаление по возрасту (только `remove(id)`).
- Граница слова в `matcher` реализована как `\b<ключ>\w*` — ловит любое слово, начинающееся с ключа (например «ремонт» ловит «ремонтник»), но так же ловит и нежелательные совпадения вроде «авто» → «автора» — известное осознанное ограничение (нет морфологического анализа), не переоткрывать как баг.

## Как здесь работает Autopilot

Сборка ведётся навыком `/autopilot`. Требования, спецификация и таски — в `.autopilot/`.
Прогресс — `.autopilot/dashboard.html`. Правило: требование из `manifest.md`
может снять только пользователь.

Если работа продолжается — скажи «продолжи автопилот»: состояние поднимется
из `.autopilot/state.js`, переспрашивать ничего не нужно.
<!-- autopilot:end -->
