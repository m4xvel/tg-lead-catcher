"""Общие тестовые фикстуры: фейковый Telethon-клиент за швом userbot.deliver
(см. interfaces.md — «Швы для тестов») и временное подключение к SQLite.
"""
from __future__ import annotations

import pytest

import store


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.message = text


class FakeEntity:
    def __init__(self, username: str | None):
        self.username = username


class FakeTelethonClient:
    """Дублирует ту часть интерфейса Telethon-клиента, которой пользуется
    userbot.deliver: forward_messages/get_messages/send_message/get_entity.
    Поведение конфигурируется полями, вызовы протоколируются в self.calls.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.sent_messages: list[tuple[int, str]] = []
        self.forward_error: Exception | None = None
        self.forward_error_for: dict[int, Exception] = {}
        self.messages: dict[int, FakeMessage] = {}
        self.entities: dict[int, FakeEntity] = {}

    async def forward_messages(self, to_chat_id, message_id, from_peer):
        self.calls.append(("forward", to_chat_id, message_id, from_peer))
        if to_chat_id in self.forward_error_for:
            raise self.forward_error_for[to_chat_id]
        if self.forward_error is not None:
            raise self.forward_error

    async def get_messages(self, chat_id, ids):
        self.calls.append(("get_messages", chat_id, ids))
        return self.messages.get(ids, FakeMessage(""))

    async def send_message(self, chat_id, text):
        self.calls.append(("send_message", chat_id, text))
        self.sent_messages.append((chat_id, text))

    async def get_entity(self, chat_id):
        self.calls.append(("get_entity", chat_id))
        return self.entities.get(chat_id, FakeEntity(None))


@pytest.fixture
def fake_client() -> FakeTelethonClient:
    return FakeTelethonClient()


@pytest.fixture
async def conn():
    connection = await store.connect(":memory:")
    yield connection
    await connection.close()
