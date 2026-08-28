"""notify_owner — единый канал уведомлений (R43/R64/R76i), используемый и тикетом 03."""
from tests.userbot.fakes import FakeTelegramClient

import userbot


async def test_notify_owner_sends_to_saved_messages():
    client = FakeTelegramClient()

    await userbot.notify_owner(client, "источник стал недоступен")

    assert client.sent_messages == [("me", "источник стал недоступен")]
