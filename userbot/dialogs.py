"""list_dialogs() — постраничный список групп/каналов пользователя (R38)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DialogInfo:
    chat_id: int
    title: str
    kind: str  # group | supergroup | channel


async def list_dialogs(
    client, *, offset: int = 0, limit: int = 8, query: str | None = None
) -> list[DialogInfo]:
    """Одна страница диалогов (группы/супергруппы/каналы), с поиском по названию.

    Личные переписки сюда не входят — R38 говорит про «мои группы/каналы»,
    личные чаты добавляются другим способом (R40). Идёт по `client.iter_dialogs()`
    и останавливается, набрав страницу — не грузит все диалоги в память разом,
    так что не упирается в сетевые таймауты на аккаунте с сотнями чатов.
    """
    page: list[DialogInfo] = []
    skipped = 0
    async for dialog in client.iter_dialogs():
        kind = _kind_of(dialog.entity)
        if kind is None:
            continue
        title = dialog.name or ""
        if query and query.lower() not in title.lower():
            continue
        if skipped < offset:
            skipped += 1
            continue
        page.append(DialogInfo(chat_id=dialog.id, title=title, kind=kind))
        if len(page) >= limit:
            break
    return page


def _kind_of(entity) -> str | None:
    from telethon.tl.types import Channel, Chat

    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        return "supergroup" if getattr(entity, "megagroup", False) else "channel"
    return None
