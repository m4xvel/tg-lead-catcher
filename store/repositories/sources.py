"""Репозиторий чатов-источников."""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from ..errors import DuplicateChatError


@dataclass(frozen=True)
class Source:
    id: int
    chat_id: int
    title: str
    kind: str
    paused: bool
    last_processed_msg_id: int | None
    added_at: str


class SourcesRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, chat_id: int, title: str, kind: str, paused: bool = False) -> Source:
        try:
            cur = await self._conn.execute(
                "INSERT INTO sources (chat_id, title, kind, paused) VALUES (?, ?, ?, ?)",
                (chat_id, title, kind, int(paused)),
            )
        except aiosqlite.IntegrityError as exc:
            raise DuplicateChatError(f"источник с chat_id={chat_id} уже добавлен") from exc
        await self._conn.commit()
        added = await self.get(id=cur.lastrowid)
        assert added is not None
        return added

    async def remove(self, id: int) -> None:
        await self._conn.execute("DELETE FROM sources WHERE id = ?", (id,))
        await self._conn.commit()

    async def get(self, *, id: int | None = None, chat_id: int | None = None) -> Source | None:
        if id is not None:
            cur = await self._conn.execute("SELECT * FROM sources WHERE id = ?", (id,))
        elif chat_id is not None:
            cur = await self._conn.execute(
                "SELECT * FROM sources WHERE chat_id = ?", (chat_id,)
            )
        else:
            raise ValueError("нужно передать id или chat_id")
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def list(self) -> list[Source]:
        cur = await self._conn.execute("SELECT * FROM sources ORDER BY id")
        rows = await cur.fetchall()
        return [self._from_row(row) for row in rows]

    async def update(
        self,
        id: int,
        *,
        title: str | None = None,
        paused: bool | None = None,
        last_processed_msg_id: int | None = None,
    ) -> None:
        fields: dict = {}
        if title is not None:
            fields["title"] = title
        if paused is not None:
            fields["paused"] = int(paused)
        if last_processed_msg_id is not None:
            fields["last_processed_msg_id"] = last_processed_msg_id
        if not fields:
            return
        set_clause = ", ".join(f"{column} = ?" for column in fields)
        await self._conn.execute(
            f"UPDATE sources SET {set_clause} WHERE id = ?", (*fields.values(), id)
        )
        await self._conn.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> Source:
        return Source(
            id=row["id"],
            chat_id=row["chat_id"],
            title=row["title"],
            kind=row["kind"],
            paused=bool(row["paused"]),
            last_processed_msg_id=row["last_processed_msg_id"],
            added_at=row["added_at"],
        )
