"""Тикет 05: `/export` (R55) через `Dispatcher.feed_update`, без реального
Telegram (см. промпт исполнителя)."""
from __future__ import annotations

import itertools
import json

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendDocument
from aiogram.types import Chat, Message, User

from panel import export as exportmod
from panel.auth import OwnerAccessMiddleware

from tests.panel.conftest import OWNER_ID, build_message_update, make_bot

_ids = itertools.count(90_000)


def _msg(text):
    return Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Влад"),
        text=text,
    )


@pytest.fixture
def dp():
    d = Dispatcher(storage=MemoryStorage())
    router = exportmod.build_router()
    router.message.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    d.include_router(router)
    return d


@pytest.fixture
def bot():
    return make_bot()


async def test_export_returns_valid_json_with_all_four_lists(dp, bot, repos):
    await repos["sources"].add(-100111, "Источник", "supergroup")
    await repos["receivers"].add(-100222, "Приёмник")
    await repos["keywords"].add("word", "ремонт")
    await repos["keywords"].add("word", "вакансия", is_stop=True)

    await dp.feed_update(bot, build_message_update(_msg("/export")), **repos)

    sent = [c for c in bot.session.calls if isinstance(c, SendDocument)]
    assert len(sent) == 1
    payload = json.loads(sent[0].document.data)
    assert set(payload.keys()) == {"sources", "keywords", "stopwords", "receivers"}
    assert payload["sources"] == [
        {"chat_id": -100111, "title": "Источник", "kind": "supergroup", "paused": False}
    ]
    assert payload["receivers"] == [{"chat_id": -100222, "title": "Приёмник"}]
    assert payload["keywords"] == [{"kind": "word", "pattern": "ремонт"}]
    assert payload["stopwords"] == [{"kind": "word", "pattern": "вакансия"}]


async def test_stranger_cannot_export(dp, bot, repos):
    await repos["sources"].add(-100111, "Источник", "supergroup")
    stranger = Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Чужой"),
        text="/export",
    )
    await dp.feed_update(bot, build_message_update(stranger), **repos)
    sent = [c for c in bot.session.calls if isinstance(c, SendDocument)]
    assert sent == []
