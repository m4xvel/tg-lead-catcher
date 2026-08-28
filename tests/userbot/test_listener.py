"""Пайплайн приёма/матчинга через фейковый Telethon-клиент (шов из interfaces.md).

Каждый тест сам регистрирует хендлеры (`register_handlers`) на `FakeTelegramClient`
и "стреляет" фейковым событием — как настоящий Telethon сделал бы на реальном
аккаунте, только без сети. Проверяется итог в `store` (hits/delivery_queue),
а не внутренности listener.py.
"""
from __future__ import annotations

from telethon import events

import store

from tests.userbot.fakes import FakeAlbumEvent, FakeEvent, FakeSender, FakeTelegramClient
from userbot.listener import Deps, register_handlers

OWNER_ID = 111
BOT_ID = 999


def make_deps(repos, ruleset):
    return Deps(
        sources=repos["sources"],
        receivers=repos["receivers"],
        hits=repos["hits"],
        delivery_queue=repos["delivery_queue"],
        settings=repos["settings"],
        ruleset_provider=lambda: ruleset,
    )


async def test_keyword_in_group_creates_hit_and_delivery_for_each_receiver(repos, ruleset):
    await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    await repos["receivers"].add(chat_id=201, title="Приёмник 1")
    await repos["receivers"].add(chat_id=202, title="Приёмник 2")

    client = FakeTelegramClient()
    register_handlers(client, make_deps(repos, ruleset))

    event = FakeEvent(
        chat_id=100, id=1, raw_text="нужен ремонт квартиры", sender_id=500,
        sender=FakeSender(id=500, username="ivan"),
    )
    await client.fire(events.NewMessage, event)

    hits = await repos["hits"].list()
    assert len(hits) == 1
    assert hits[0].source_chat_id == 100 and hits[0].message_id == 1

    tasks = await repos["delivery_queue"].list()
    assert sorted(t.receiver_chat_id for t in tasks) == [201, 202]


async def test_private_chat_ignored_unless_flag_enabled(repos, ruleset):
    await repos["sources"].add(chat_id=300, title="ЛС", kind="private")
    client = FakeTelegramClient()
    deps = make_deps(repos, ruleset)
    register_handlers(client, deps)

    event = FakeEvent(chat_id=300, id=1, raw_text="нужен ремонт", sender_id=500,
                       sender=FakeSender(id=500))
    await client.fire(events.NewMessage, event)
    assert await repos["hits"].list() == []

    await repos["settings"].set("monitor_all_dm", "1")
    event2 = FakeEvent(chat_id=300, id=2, raw_text="нужен ремонт", sender_id=500,
                        sender=FakeSender(id=500))
    await client.fire(events.NewMessage, event2)
    assert len(await repos["hits"].list()) == 1


async def test_caption_matches_like_text(repos, ruleset):
    await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    client = FakeTelegramClient()
    register_handlers(client, make_deps(repos, ruleset))

    event = FakeEvent(
        chat_id=100, id=1, raw_text="фото объекта, нужен ремонт", sender_id=500,
        sender=FakeSender(id=500),
    )
    await client.fire(events.NewMessage, event)

    assert len(await repos["hits"].list()) == 1


async def test_album_creates_single_hit_not_one_per_photo(repos, ruleset):
    await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    client = FakeTelegramClient()
    register_handlers(client, make_deps(repos, ruleset))

    photos = [
        FakeEvent(chat_id=100, id=10, raw_text="", sender_id=500, grouped_id=77),
        FakeEvent(chat_id=100, id=11, raw_text="нужен ремонт", sender_id=500, grouped_id=77),
        FakeEvent(chat_id=100, id=12, raw_text="", sender_id=500, grouped_id=77),
    ]
    # NewMessage тоже стреляет для каждого фото альбома — хендлер обязан их игнорировать.
    for photo in photos:
        await client.fire(events.NewMessage, photo)

    album_event = FakeAlbumEvent(chat_id=100, messages=photos, sender_id=500,
                                  sender=FakeSender(id=500))
    await client.fire(events.Album, album_event)

    hits = await repos["hits"].list()
    assert len(hits) == 1
    assert hits[0].message_id == 10  # id первого сообщения альбома


async def test_edit_adding_keyword_hits_once_reprocessing_same_id_does_not(repos, ruleset):
    await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    client = FakeTelegramClient()
    register_handlers(client, make_deps(repos, ruleset))

    # Исходное сообщение без ключа не порождает hit.
    original = FakeEvent(chat_id=100, id=1, raw_text="просто сообщение", sender_id=500,
                          sender=FakeSender(id=500))
    await client.fire(events.NewMessage, original)
    assert await repos["hits"].list() == []

    edited = FakeEvent(chat_id=100, id=1, raw_text="просто сообщение, нужен ремонт",
                        sender_id=500, sender=FakeSender(id=500))
    await client.fire(events.MessageEdited, edited)
    assert len(await repos["hits"].list()) == 1

    # Повторная обработка того же message_id (например, второй edit) не задваивает.
    edited_again = FakeEvent(chat_id=100, id=1, raw_text="просто сообщение, нужен ремонт срочно",
                              sender_id=500, sender=FakeSender(id=500))
    await client.fire(events.MessageEdited, edited_again)
    assert len(await repos["hits"].list()) == 1


async def test_own_bot_and_receiver_chat_messages_never_hit(repos, ruleset):
    await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    await repos["receivers"].add(chat_id=201, title="Приёмник")
    await repos["sources"].add(chat_id=201, title="Чат-приёмник как источник", kind="group")
    client = FakeTelegramClient()
    register_handlers(client, make_deps(repos, ruleset))

    own = FakeEvent(chat_id=100, id=1, raw_text="нужен ремонт", sender_id=OWNER_ID, out=True,
                     sender=FakeSender(id=OWNER_ID))
    bot_msg = FakeEvent(chat_id=100, id=2, raw_text="нужен ремонт", sender_id=BOT_ID,
                         sender=FakeSender(id=BOT_ID, bot=True))
    in_receiver = FakeEvent(chat_id=201, id=3, raw_text="нужен ремонт", sender_id=500,
                             sender=FakeSender(id=500))

    for event in (own, bot_msg, in_receiver):
        await client.fire(events.NewMessage, event)

    assert await repos["hits"].list() == []
