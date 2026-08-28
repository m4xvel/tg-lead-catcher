"""Репозиторий чатов-приёмников."""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from ..errors import DuplicateChatError


@dataclass(frozen=True)
class Receiver:
    id: int
    chat_id: int
    title: str
    added_at: str


class ReceiversRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, chat_id: int, title: str) -> Receiver:
        try:
            cur = await self._conn.execute(
                "INSERT INTO receivers (chat_id, title) VALUES (?, ?)",
                (chat_id, title),
            )
        except aiosqlite.IntegrityError as exc:
            raise DuplicateChatError(f"приёмник с chat_id={chat_id} уже добавлен") from exc
        await self._conn.commit()
        added = await self.get(id=cur.lastrowid)
        assert added is not None
        return added

    async def remove(self, id: int) -> None:
        await self._conn.execute("DELETE FROM receivers WHERE id = ?", (id,))
        await self._conn.commit()

    async def get(self, *, id: int | None = None, chat_id: int | None = None) -> Receiver | None:
        if id is not None:
            cur = await self._conn.execute("SELECT * FROM receivers WHERE id = ?", (id,))
        elif chat_id is not None:
            cur = await self._conn.execute(
                "SELECT * FROM receivers WHERE chat_id = ?", (chat_id,)
            )
        else:
            raise ValueError("нужно передать id или chat_id")
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def list(self) -> list[Receiver]:
        cur = await self._conn.execute("SELECT * FROM receivers ORDER BY id")
        rows = await cur.fetchall()
        return [self._from_row(row) for row in rows]

    async def update(self, id: int, *, title: str | None = None) -> None:
        if title is None:
            return
        await self._conn.execute(
            "UPDATE receivers SET title = ? WHERE id = ?", (title, id)
        )
        await self._conn.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> Receiver:
        return Receiver(
            id=row["id"], chat_id=row["chat_id"], title=row["title"], added_at=row["added_at"]
        )
