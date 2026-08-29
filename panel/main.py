"""Точка входа `python -m panel.main` (Решения §1, R07, R11, R65).

Поднимает aiogram-диспетчер и подключает единый роутер панели
(`panel.build_router`, тикеты 04+05 — источники/приёмники/ключи/статистика/
экспорт, уже в правильном порядке для R53, см. `panel/__init__.py`).
Панели для `userbot.list_dialogs`/`userbot.catchup.scan_source` нужен свой
Telethon-клиент — процесс отдельный от `userbot.main`, поэтому здесь
собственное подключение к уже готовой сессии (`setup.py` логинит её заранее,
здесь только `client.start()`, без интерактивного ввода).

`userbot.main` и `panel.main` — два разных процесса (см. `docker-compose.yml`),
делящих один том `./data/`. Два Telethon-клиента, читающих/пишущих один и тот
же файл сессии из разных процессов одновременно — гонка/повреждение SQLite,
против которой предупреждает сам Telethon. Поэтому у panel — СВОЯ сессия,
`./data/panel.session`, симметричная userbot-овской `./data/userbot.session`
(см. `userbot/client.py:DATA_DIR`/`SESSION_NAME`), но независимо
авторизованная (`setup.py` логинит её вторым шагом, тем же номером телефона —
Telegram нормально поддерживает несколько параллельных сессий на одном
аккаунте, как Desktop и Web одновременно).

`build()` собирает диспетчер/бота/зависимости и НЕ запускает polling — швов
для тестов достаточно на этом уровне (см. промпт исполнителя: не крутить
`start_polling()` в тестах). `run()` — тонкая обвязка поверх `build()`,
блокируется на `dp.start_polling(...)` последним шагом.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import panel
import store
from userbot import build_client
from userbot.client import DATA_DIR

logger = logging.getLogger("panel.main")

DB_PATH = "./data/bot.db"

# Имя сессии panel — симметрично `userbot.client.SESSION_NAME` ("userbot"),
# но отдельный файл: два процесса не должны делить один *.session (см. модуль-докстринг).
PANEL_SESSION_NAME = "panel"


async def build(*, db_path: str | None = None, bot: Bot | None = None, tg_client=None):
    """Собирает `(dp, bot, repos, tg_client)`. `db_path`/`bot`/`tg_client` —
    точки подмены для тестов, без них — конфигурация по умолчанию/реальные
    Bot API и Telethon.
    """
    conn = await store.connect(db_path or DB_PATH)
    repos = {
        "sources": store.SourcesRepo(conn),
        "receivers": store.ReceiversRepo(conn),
        "keywords": store.KeywordsRepo(conn),
        "hits": store.HitsRepo(conn),
        "delivery_queue": store.DeliveryQueueRepo(conn),
        "settings": store.SettingsRepo(conn),
    }

    owner_id = int(os.environ["OWNER_ID"])

    if tg_client is None:
        tg_client = build_client(session_path=str(DATA_DIR / PANEL_SESSION_NAME))
        await tg_client.start()

    if bot is None:
        bot = Bot(token=os.environ["BOT_TOKEN"])

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(panel.build_router(owner_id))

    return dp, bot, repos, tg_client


async def run(*, db_path: str | None = None, bot: Bot | None = None, tg_client=None) -> None:
    dp, bot, repos, tg_client = await build(db_path=db_path, bot=bot, tg_client=tg_client)
    logger.info("panel: старт polling")
    await dp.start_polling(bot, **repos, tg_client=tg_client)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
