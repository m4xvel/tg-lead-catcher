"""Резолв чата для «➕ Добавить чат» (R38-R40, R40.1): ручной ввод и форвард.
Без сети — Bot API замокан фейком, forward_origin собран вручную (executor.md:
хендлеры проверяются на сфабрикованных объектах, а не на реальном Telegram).
"""
from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat
from aiogram.types import Chat, MessageOriginChannel, MessageOriginChat, MessageOriginUser, User

from panel.sources import (
    ChatResolutionError,
    extract_forwarded_chat,
    parse_chat_reference,
    resolve_manual_chat,
)


# --- parse_chat_reference (чистая функция, без сети) -----------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@leed_chat", "@leed_chat"),
        ("https://t.me/leed_chat", "@leed_chat"),
        ("t.me/leed_chat", "@leed_chat"),
        ("-1001234567890", -1001234567890),
        ("123456", 123456),
        ("t.me/c/1234567890", -1001234567890),
    ],
)
def test_parse_chat_reference_accepts_all_three_formats(text, expected):
    assert parse_chat_reference(text) == expected


def test_parse_chat_reference_rejects_garbage():
    with pytest.raises(ChatResolutionError):
        parse_chat_reference("не чат вообще")


# --- resolve_manual_chat (через Bot API get_chat) ---------------------------


class FakeBot:
    def __init__(self, chat: Chat | None = None, error: Exception | None = None):
        self._chat = chat
        self._error = error
        self.calls: list = []

    async def __call__(self, method):
        self.calls.append(method)
        if isinstance(method, GetChat):
            if self._error:
                raise self._error
            return self._chat
        raise AssertionError(f"unexpected method {method}")

    async def get_chat(self, chat_id):
        return await self(GetChat(chat_id=chat_id))


async def test_resolve_manual_chat_returns_resolved_chat_on_success():
    chat = Chat(id=-1009876543210, type="supergroup", title="Тестовая группа")
    bot = FakeBot(chat=chat)

    resolved = await resolve_manual_chat(bot, "@leed_chat")

    assert resolved.chat_id == -1009876543210
    assert resolved.title == "Тестовая группа"
    assert resolved.kind == "supergroup"


async def test_resolve_manual_chat_raises_friendly_error_when_unreachable():
    bot = FakeBot(error=TelegramBadRequest(method=GetChat(chat_id="@nope"), message="chat not found"))

    with pytest.raises(ChatResolutionError):
        await resolve_manual_chat(bot, "@nope")


async def test_resolve_manual_chat_rejects_bad_format_without_calling_bot():
    bot = FakeBot()

    with pytest.raises(ChatResolutionError):
        await resolve_manual_chat(bot, "просто текст без формата")

    assert bot.calls == []


# --- extract_forwarded_chat --------------------------------------------------


def _msg_with_origin(origin):
    class _M:
        forward_origin = origin

    return _M()


def test_extract_forwarded_chat_from_channel_post():
    origin = MessageOriginChannel(
        date=0, chat=Chat(id=-1001111111111, type="channel", title="Канал X"), message_id=5
    )
    resolved = extract_forwarded_chat(_msg_with_origin(origin))
    assert resolved.chat_id == -1001111111111
    assert resolved.kind == "channel"
    assert resolved.title == "Канал X"


def test_extract_forwarded_chat_from_anonymous_group_post():
    origin = MessageOriginChat(date=0, sender_chat=Chat(id=-1002222222222, type="supergroup", title="Группа Y"))
    resolved = extract_forwarded_chat(_msg_with_origin(origin))
    assert resolved.chat_id == -1002222222222
    assert resolved.kind == "supergroup"


def test_extract_forwarded_chat_from_private_user():
    origin = MessageOriginUser(date=0, sender_user=User(id=42, is_bot=False, first_name="Иван"))
    resolved = extract_forwarded_chat(_msg_with_origin(origin))
    assert resolved.chat_id == 42
    assert resolved.kind == "private"
    assert resolved.title == "Иван"


def test_extract_forwarded_chat_returns_none_without_forward_origin():
    assert extract_forwarded_chat(_msg_with_origin(None)) is None
