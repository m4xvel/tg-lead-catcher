"""Точка входа `python -m userbot.main` (Решения §1, R06, R11, R65).

Поднимает Telethon-клиент на уже созданной `setup.py` сессии (`client.start()`
без интерактивного логина), один раз при старте догоняет пропущенное по всем
активным источникам (тикет 02, R35/R54/R76i), регистрирует live-хендлеры
(тикет 02) и запускает фоновый цикл доставки `worker.run()` (тикет 03) —
по спецификации `worker` живёт внутри процесса `userbot`, не отдельным
сервисом. Дальше слушает вечно, пока процесс жив (`docker-compose.yml`:
`restart: unless-stopped`, R65).

`run()` собирает всё и блокируется на `client.run_until_disconnected()`
последним шагом — тесты вызывают `run()` с фейковым `client`
(`run_until_disconnected`/`start` не ходят в сеть), проверяя, что до
блокирующего вызова остальная сборка отработала в правильном порядке.

Фоновые задачи (`refresh_loop`/`worker.run`/`retention.run_daily`) создаются
через `_spawn_tracked_task`, а не голым `asyncio.create_task(...)`: цикл
событий держит только СЛАБУЮ ссылку на «ничейную» задачу — без сильной ссылки
где-то в живом объекте GC может собрать задачу на середине выполнения, без
ошибки и без следа (см. документацию `asyncio.create_task`). Это тихо ломало
бы R65 («продолжает работать»)/R68 («чистится сами»). `_spawn_tracked_task`
держит ссылку в `background_tasks` до завершения задачи и логирует (плюс,
по возможности, уведомляет владельца) любое падение не-по-отмене — по тому
же принципу, что и остальной проект («упавший фоновый процесс не молчит»,
см. `worker.run`/`userbot.catchup`).
"""
from __future__ import annotations

import asyncio
import logging
import os

import matcher
import store
import worker
from userbot import (
    Deps,
    build_client,
    register_handlers,
    run_startup_catchup,
)

logger = logging.getLogger("userbot.main")

DB_PATH = "./data/bot.db"

# Пауза мониторинга (R48) обновляет ключи в БД из другого процесса (panel) —
# ruleset_provider обязан быть синхронным (Deps.ruleset_provider), поэтому
# держим готовый Ruleset в памяти и пересчитываем его периодически в фоне,
# а не при каждом сообщении (см. interfaces.md: «пересчитывалось на
# актуальные ключи, а не фиксировалось при старте»).
RULESET_REFRESH_SECONDS = 30.0


def _make_ruleset_provider(keywords: "store.KeywordsRepo"):
    """Возвращает `(ruleset_provider, refresh, refresh_loop)`.

    `ruleset_provider` — синхронный `() -> matcher.Ruleset`, читает закэшированное
    значение; `refresh()` пересчитывает его из БД (async); `refresh_loop()` —
    бесконечный цикл, вызывающий `refresh()` каждые `RULESET_REFRESH_SECONDS`.
    """
    state: dict[str, matcher.Ruleset] = {"ruleset": matcher.compile([], [])}

    async def refresh() -> None:
        kws = await keywords.list(is_stop=False)
        stops = await keywords.list(is_stop=True)
        state["ruleset"] = matcher.compile(
            [(k.kind, k.pattern) for k in kws],
            [(k.kind, k.pattern) for k in stops],
        )

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(RULESET_REFRESH_SECONDS)
            await refresh()

    return (lambda: state["ruleset"]), refresh, refresh_loop


def _spawn_tracked_task(
    background_tasks: set[asyncio.Task], coro, name: str
) -> asyncio.Task:
    """Создаёт `asyncio.Task` и держит на неё сильную ссылку в `background_tasks`,
    пока она не завершится, плюс регистрирует `_report_background_task_failure`
    как `add_done_callback` (стандартный паттерн из документации `asyncio`:
    без ссылки где-то в живом объекте GC может собрать «ничейную» задачу на
    середине выполнения — без ошибки и без следа).
    """
    task = asyncio.create_task(coro, name=name)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    task.add_done_callback(lambda t: _report_background_task_failure(t, name, background_tasks))
    return task


def _report_background_task_failure(
    task: asyncio.Task, name: str, background_tasks: set[asyncio.Task]
) -> None:
    """Если фоновая задача упала не из-за штатной отмены — не дать ей исчезнуть
    молча (R65/R68): логируем и, если дёшево (переменные окружения уже есть),
    уведомляем владельца через тот же резервный канал, что и при смерти
    userbot-сессии (`worker.bot_notify.notify_owner_via_bot_api`).
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return

    logger.error("userbot: фоновая задача %r упала с исключением", name, exc_info=exc)

    bot_token = os.environ.get("BOT_TOKEN")
    owner_id_raw = os.environ.get("OWNER_ID")
    if not bot_token or not owner_id_raw:
        # TODO: без BOT_TOKEN/OWNER_ID в окружении уведомить владельца нечем —
        # падение фоновой задачи остаётся только в логах.
        return

    text = f"userbot: фоновая задача '{name}' упала с исключением: {exc!r}"
    notify_task = asyncio.create_task(
        worker.bot_notify.notify_owner_via_bot_api(bot_token, int(owner_id_raw), text)
    )
    # Уведомление о падении — тоже фоновая задача; держим на неё ссылку тем же
    # способом, чтобы GC не съел её раньше отправки.
    background_tasks.add(notify_task)
    notify_task.add_done_callback(background_tasks.discard)


def _make_retention_days_getter(settings: "store.SettingsRepo"):
    """Возвращает async `() -> int`, читающий `settings.retention_days`
    (дефолт 90 — как посеяно в `store.db.create_schema`, R68)."""

    async def retention_days_getter() -> int:
        return int(await settings.get("retention_days", "90"))

    return retention_days_getter


async def run(*, db_path: str | None = None, client=None) -> None:
    """Собирает и запускает процесс userbot. `db_path`/`client` — точки
    подмены для тестов, без них — конфигурация по умолчанию/реальный Telethon.
    """
    conn = await store.connect(db_path or DB_PATH)
    sources = store.SourcesRepo(conn)
    receivers = store.ReceiversRepo(conn)
    keywords = store.KeywordsRepo(conn)
    hits = store.HitsRepo(conn)
    delivery_queue = store.DeliveryQueueRepo(conn)
    settings = store.SettingsRepo(conn)

    if client is None:
        client = build_client()
    await client.start()
    logger.info("userbot: клиент подключён")

    ruleset_provider, refresh_ruleset, refresh_loop = _make_ruleset_provider(keywords)
    await refresh_ruleset()

    deps = Deps(
        sources=sources,
        receivers=receivers,
        hits=hits,
        delivery_queue=delivery_queue,
        settings=settings,
        ruleset_provider=ruleset_provider,
    )

    logger.info("userbot: догоняю пропущенное по активным источникам")
    await run_startup_catchup(
        client,
        sources=sources,
        receivers=receivers,
        hits=hits,
        delivery_queue=delivery_queue,
        settings=settings,
        ruleset_provider=ruleset_provider,
    )

    register_handlers(client, deps)
    logger.info("userbot: live-хендлеры зарегистрированы")

    # Сильные ссылки на фоновые задачи живут в кадре `run()` до самого конца
    # (он блокируется на `run_until_disconnected()` ниже) — без этого цикл
    # событий держал бы только слабую ссылку, и GC мог бы собрать задачу на
    # середине выполнения (см. докстринг модуля, R65/R68).
    background_tasks: set[asyncio.Task] = set()
    _spawn_tracked_task(background_tasks, refresh_loop(), "refresh_loop")
    _spawn_tracked_task(background_tasks, worker.run(conn, client), "worker.run")
    _spawn_tracked_task(
        background_tasks,
        worker.retention.run_daily(
            conn,
            retention_days_getter=_make_retention_days_getter(settings),
            sleep=asyncio.sleep,
        ),
        "retention.run_daily",
    )
    logger.info("userbot: цикл доставки запущен, слушаю новые сообщения")

    await client.run_until_disconnected()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
