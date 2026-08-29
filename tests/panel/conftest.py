"""Общие фикстуры для тестов панели: фейковый Bot API (без сети) и фейковый
Telethon-клиент (`userbot.list_dialogs`/`scan_source`/`get_me`), плюс сборщики
Update-объектов для `Dispatcher.feed_update` — так тесты гоняют реальные
хендлеры aiogram без подключения к настоящему Telegram (см. промпт исполнителя).
"""
from __future__ import annotations

import itertools

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetChat,
    SendDocument,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import Chat, Message, Update

import store
from tests.conftest import FakeEntity

OWNER_ID = 555111


class FakeSession(BaseSession):
    """Подменяет сетевой транспорт aiogram: отвечает на `get_chat` из `chats`
    (по chat_id/username); `send_message`/`edit_message_text`/`answer_callback_query`
    (то, чем реально пользуются хендлеры панели, отвечая пользователю) получают
    правдоподобные фейковые ответы — сами тексты/клавиатуры проверяются на
    уровне store/keyboards, не через содержимое этих Bot API-ответов.
    """

    def __init__(self, chats: dict | None = None, error: Exception | None = None):
        super().__init__()
        self.chats = chats or {}
        self.error = error
        self.calls: list[TelegramMethod] = []
        self._msg_ids = itertools.count(1)

    async def close(self) -> None:
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - не используется
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetChat):
            if self.error is not None:
                raise self.error
            key = method.chat_id
            if key in self.chats:
                return self.chats[key]
            raise KeyError(f"фейковая сессия не знает чат {key!r}")
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=next(self._msg_ids), date=0, chat=Chat(id=method.chat_id, type="private")
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, SendDocument):
            # Тикет 05 (`/export`, R55): фейковый ответ на отправку файла — сам
            # файл (`method.document`) тесты читают из `self.calls`, не отсюда.
            return Message(
                message_id=next(self._msg_ids), date=0, chat=Chat(id=method.chat_id, type="private")
            )
        raise AssertionError(f"неожиданный метод Bot API в тесте: {method}")


def make_bot(*, chats: dict | None = None, error: Exception | None = None) -> Bot:
    return Bot(token="123456:TEST-TOKEN", session=FakeSession(chats=chats, error=error))


class FakeDialog:
    def __init__(self, chat_id: int, name: str, entity):
        self.id = chat_id
        self.name = name
        self.entity = entity


def supergroup_entity():
    """`userbot.dialogs._kind_of` распознаёт только настоящие типы Telethon —
    как в tests/userbot/test_dialogs.py, эта фабрика делает то же самое."""
    from telethon.tl.types import Channel

    entity = Channel.__new__(Channel)
    entity.megagroup = True
    return entity


def channel_entity():
    from telethon.tl.types import Channel

    entity = Channel.__new__(Channel)
    entity.megagroup = False
    return entity


# Обратная совместимость по стилю с userbot-тестами — вызываемые фабрики, а не
# статические классы, потому что `Channel.__new__` нужно вызывать каждый раз.
FakeSupergroup = supergroup_entity
FakeChannel = channel_entity


class FakeTelethonClient:
    """Дублирует интерфейс Telethon, которым пользуется `userbot.list_dialogs`
    (`iter_dialogs`), `userbot.catchup.catch_up`/`scan_source` (`iter_messages`),
    `panel.receivers.ensure_default_receiver` (`get_me`) и
    `userbot.deliver.build_original_link`, которой пользуется «История»
    (`panel.stats.format_history`, R30/R52) (`get_entity`).
    """

    def __init__(
        self,
        dialogs: list[FakeDialog] | None = None,
        me_id: int = 999,
        entities: dict[int, FakeEntity] | None = None,
    ):
        self._dialogs = dialogs or []
        self._me_id = me_id
        self.get_me_calls = 0
        self.entities = entities or {}
        self.get_entity_calls: list[int] = []

    async def iter_dialogs(self):
        for d in self._dialogs:
            yield d

    async def iter_messages(self, chat_id, **kwargs):
        return
        yield  # pragma: no cover - пустой async-генератор (сообщений нет)

    async def get_me(self):
        self.get_me_calls += 1

        class _Me:
            id = self._me_id

        return _Me()

    async def get_entity(self, chat_id):
        self.get_entity_calls.append(chat_id)
        return self.entities.get(chat_id, FakeEntity(None))


_update_ids = itertools.count(1)


def build_message_update(message) -> Update:
    return Update(update_id=next(_update_ids), message=message)


def build_callback_update(callback_query) -> Update:
    return Update(update_id=next(_update_ids), callback_query=callback_query)


@pytest.fixture
async def conn():
    connection = await store.connect(":memory:")
    yield connection
    await connection.close()


@pytest.fixture
def repos(conn):
    return {
        "sources": store.SourcesRepo(conn),
        "receivers": store.ReceiversRepo(conn),
        "keywords": store.KeywordsRepo(conn),
        "hits": store.HitsRepo(conn),
        "delivery_queue": store.DeliveryQueueRepo(conn),
        "settings": store.SettingsRepo(conn),
    }
