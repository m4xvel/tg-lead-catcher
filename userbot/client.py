"""Telethon-клиент userbot: создание сессии и единый канал уведомлений владельцу.

Уведомление владельцу (R43, R64, R76i) уходит через тот же аккаунт, от которого
работает userbot — сообщением в «Избранное» (`send_message("me", ...)`). Не нужен
второй токен/сессия. Тикет 03 обязан звать `notify_owner` из этого модуля для
уведомлений о недоступных приёмниках — не изобретать второй канал (см. R43 в
interfaces.md).
"""
from __future__ import annotations

import os
from pathlib import Path

from telethon import TelegramClient

DATA_DIR = Path("./data")
SESSION_NAME = "userbot"


def build_client(session_path: str | None = None) -> TelegramClient:
    """Создаёт (не подключает и не логинит) Telethon-клиент по API_ID/API_HASH из окружения.

    Сессия хранится в ``./data/userbot.session`` (R66). Подключение/логин по номеру
    телефона — забота `setup.py`/точки входа, не этой функции.
    """
    api_id = os.environ["API_ID"]
    api_hash = os.environ["API_HASH"]
    path = session_path or str(DATA_DIR / SESSION_NAME)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(path, int(api_id), api_hash)


async def notify_owner(client, text: str) -> None:
    """Единый канал уведомлений владельцу — сообщение в «Избранное» того же аккаунта.

    ``client`` — Telethon-клиент (или фейк с тем же интерфейсом `send_message`).
    """
    await client.send_message("me", text)
