"""Догон истории: без предохранителя при старте (R35/R54), с предохранителем по
кнопке/снятию паузы (R37/R78i), пометка недоступного источника + одно уведомление (R76i).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from telethon import errors as tg_errors

import matcher

from userbot.catchup import HARD_MSG_CAP, run_startup_catchup, scan_source


@dataclass
class FakeHistoryMessage:
    chat_id: int
    id: int
    raw_text: str
    sender_id: int | None = None
    out: bool = False
    date=None

    async def get_sender(self):
        return None


class FakeHistoryClient:
    """Фейковый клиент с iter_messages по заранее заданной истории чата."""

    def __init__(self, messages_by_chat: dict[int, list[FakeHistoryMessage]]):
        self._messages_by_chat = messages_by_chat
        self.sent_messages: list[tuple[str, str]] = []

    async def iter_messages(self, chat_id, *, reverse=True, min_id=None):
        history = self._messages_by_chat.get(chat_id, [])
        for message in history:
            if min_id is not None and message.id <= min_id:
                continue
            yield message

    async def send_message(self, chat, text):
        self.sent_messages.append((chat, text))


class FailingHistoryClient:
    """Симулирует источник, ставший недоступным (кикнули/чат удалён)."""

    def __init__(self, exc):
        self._exc = exc
        self.sent_messages: list[tuple[str, str]] = []

    async def iter_messages(self, chat_id, *, reverse=True, min_id=None):
        raise self._exc
        yield  # pragma: no cover - делает функцию генератором

    async def send_message(self, chat, text):
        self.sent_messages.append((chat, text))


def deps(repos, ruleset):
    return dict(
        sources=repos["sources"],
        receivers=repos["receivers"],
        hits=repos["hits"],
        delivery_queue=repos["delivery_queue"],
        settings=repos["settings"],
        ruleset_provider=lambda: ruleset,
    )


async def test_startup_catchup_reads_all_missed_messages_without_upper_bound(repos, ruleset):
    source = await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    await repos["sources"].update(source.id, last_processed_msg_id=5)

    history = [
        FakeHistoryMessage(chat_id=100, id=i, raw_text="нужен ремонт" if i % 50 == 0 else "шум")
        for i in range(6, 6 + 600)  # намеренно больше 500 — старт без предохранителя (R35)
    ]
    client = FakeHistoryClient({100: history})

    await run_startup_catchup(client, pause_seconds=0, **deps(repos, ruleset))

    hits = await repos["hits"].list()
    matched_ids = {h.message_id for h in hits}
    assert matched_ids == {i for i in range(6, 6 + 600) if i % 50 == 0}

    updated_source = await repos["sources"].get(id=source.id)
    assert updated_source.last_processed_msg_id == 6 + 600 - 1


async def test_manual_scan_caps_at_hard_limit_regardless_of_settings(repos, ruleset):
    source = await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    await repos["settings"].set("scan_msgs", "10000")  # неверно настроено — всё равно не больше 500

    history = [
        FakeHistoryMessage(chat_id=100, id=i, raw_text="шум") for i in range(1, 1000)
    ]
    client = FakeHistoryClient({100: history})

    await scan_source(client, source, pause_seconds=0, limit_days=3650, **deps(repos, ruleset))

    updated_source = await repos["sources"].get(id=source.id)
    # ровно HARD_MSG_CAP сообщений просканировано (id с 1 по HARD_MSG_CAP)
    assert updated_source.last_processed_msg_id == HARD_MSG_CAP


async def test_inaccessible_source_is_paused_and_notified_once(repos, ruleset):
    source = await repos["sources"].add(chat_id=100, title="Пропавший чат", kind="group")
    client = FailingHistoryClient(tg_errors.ChannelPrivateError(request=None))

    await run_startup_catchup(client, pause_seconds=0, **deps(repos, ruleset))

    updated_source = await repos["sources"].get(id=source.id)
    assert updated_source.paused is True
    assert len(client.sent_messages) == 1
    assert client.sent_messages[0][0] == "me"

    # Повторный прогон не трогает уже приостановленный источник — уведомление не повторяется.
    await run_startup_catchup(client, pause_seconds=0, **deps(repos, ruleset))
    assert len(client.sent_messages) == 1


async def test_unrelated_value_error_is_not_treated_as_inaccessible_source(repos, ruleset):
    """Голый ValueError — это баг где-то ещё, а не "источник недоступен":
    источник не паузится, ошибка всплывает наружу (иначе реальная причина теряется)."""
    source = await repos["sources"].add(chat_id=100, title="Чат", kind="group")
    client = FailingHistoryClient(ValueError("что-то сломалось не из-за недоступности чата"))

    with pytest.raises(ValueError):
        await run_startup_catchup(client, pause_seconds=0, **deps(repos, ruleset))

    updated_source = await repos["sources"].get(id=source.id)
    assert updated_source.paused is False


async def test_unpausing_source_resumes_from_saved_place(repos, ruleset):
    source = await repos["sources"].add(
        chat_id=100, title="Чат", kind="group", paused=True
    )
    await repos["sources"].update(source.id, last_processed_msg_id=5)

    history = [
        FakeHistoryMessage(chat_id=100, id=i, raw_text="старое, нужен ремонт")
        for i in range(1, 5)
    ] + [
        FakeHistoryMessage(chat_id=100, id=6, raw_text="новое, нужен ремонт"),
    ]
    client = FakeHistoryClient({100: history})

    await repos["sources"].update(source.id, paused=False)
    fresh = await repos["sources"].get(id=source.id)
    await scan_source(client, fresh, pause_seconds=0, **deps(repos, ruleset))

    hits = await repos["hits"].list()
    # Только сообщение после last_processed_msg_id=5 обработано, старые (id<=5) — нет.
    assert [h.message_id for h in hits] == [6]
