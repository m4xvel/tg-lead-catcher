"""list_dialogs() — постранично, без загрузки сотен чатов разом (R38)."""
from __future__ import annotations

from dataclasses import dataclass

from userbot.dialogs import list_dialogs


class _FakeChat:
    pass


class _FakeChannel:
    def __init__(self, megagroup=False):
        self.megagroup = megagroup


class _FakeUser:
    pass


@dataclass
class _FakeDialog:
    id: int
    name: str
    entity: object


class RecordingDialogsClient:
    """Считает, сколько диалогов реально перебрано (эмулирует сеть на сотнях чатов)."""

    def __init__(self, dialogs):
        self._dialogs = dialogs
        self.iterated = 0

    async def iter_dialogs(self):
        for dialog in self._dialogs:
            self.iterated += 1
            yield dialog


def _make_chat_types():
    from telethon.tl.types import Channel, Chat

    return Chat, Channel


def _dialog(i, kind):
    Chat, Channel = _make_chat_types()
    if kind == "group":
        entity = Chat.__new__(Chat)
    elif kind == "supergroup":
        entity = Channel.__new__(Channel)
        entity.megagroup = True
    elif kind == "channel":
        entity = Channel.__new__(Channel)
        entity.megagroup = False
    else:
        entity = _FakeUser()
    return _FakeDialog(id=1000 + i, name=f"Чат {i}", entity=entity)


async def test_list_dialogs_returns_one_page_without_scanning_everything():
    dialogs = [_dialog(i, "group") for i in range(300)]  # "сотни чатов"
    client = RecordingDialogsClient(dialogs)

    page = await list_dialogs(client, offset=0, limit=8)

    assert len(page) == 8
    assert client.iterated == 8  # не прошёл все 300 разом


async def test_list_dialogs_offset_moves_to_next_page():
    dialogs = [_dialog(i, "group") for i in range(20)]
    client = RecordingDialogsClient(dialogs)

    page1 = await list_dialogs(client, offset=0, limit=8)
    page2 = await list_dialogs(RecordingDialogsClient(dialogs), offset=8, limit=8)

    assert [d.chat_id for d in page1] != [d.chat_id for d in page2]


async def test_list_dialogs_filters_by_title_query():
    dialogs = [_dialog(i, "group") for i in range(5)]
    dialogs[2] = _FakeDialog(id=2002, name="Ремонтники СПб", entity=dialogs[2].entity)
    client = RecordingDialogsClient(dialogs)

    page = await list_dialogs(client, query="ремонт")

    assert [d.title for d in page] == ["Ремонтники СПб"]


async def test_list_dialogs_skips_private_users():
    dialogs = [_dialog(0, "private"), _dialog(1, "group")]
    client = RecordingDialogsClient(dialogs)

    page = await list_dialogs(client)

    assert len(page) == 1
    assert page[0].kind == "group"
