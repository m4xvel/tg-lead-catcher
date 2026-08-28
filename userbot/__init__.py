"""Userbot: сессия Telethon, приём и матчинг сообщений, догон истории, список диалогов.

Исполнение доставки (`deliver`) — зона тикета 03, здесь не реализуется; этот пакет
только кладёт запись в `delivery_queue` через `store`.
"""
from .client import build_client, notify_owner
from .dialogs import DialogInfo, list_dialogs
from .catchup import catch_up, run_startup_catchup
from .listener import Deps, IncomingMessage, process, register_handlers

__all__ = [
    "build_client",
    "notify_owner",
    "DialogInfo",
    "list_dialogs",
    "catch_up",
    "run_startup_catchup",
    "Deps",
    "IncomingMessage",
    "process",
    "register_handlers",
]
