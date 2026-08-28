"""Репозиторий очереди доставки. Ретраи/бэкофф/лимит — забота worker (тикет вне зоны)."""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True)
class DeliveryTask:
    id: int
    hit_id: int
    receiver_chat_id: int
    status: str
    attempts: int
    next_attempt_at: str | None
    error: str | None


class DeliveryQueueRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, hit_id: int, receiver_chat_id: int) -> DeliveryTask:
        cur = await self._conn.execute(
            "INSERT INTO delivery_queue (hit_id, receiver_chat_id) VALUES (?, ?)",
            (hit_id, receiver_chat_id),
        )
        await self._conn.commit()
        added = await self.get(cur.lastrowid)
        assert added is not None
        return added

    async def remove(self, id: int) -> None:
        await self._conn.execute("DELETE FROM delivery_queue WHERE id = ?", (id,))
        await self._conn.commit()

    async def get(self, id: int) -> DeliveryTask | None:
        cur = await self._conn.execute("SELECT * FROM delivery_queue WHERE id = ?", (id,))
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def list(self, status: str | None = None) -> list[DeliveryTask]:
        if status is None:
            cur = await self._conn.execute("SELECT * FROM delivery_queue ORDER BY id")
        else:
            cur = await self._conn.execute(
                "SELECT * FROM delivery_queue WHERE status = ? ORDER BY id", (status,)
            )
        rows = await cur.fetchall()
        return [self._from_row(row) for row in rows]

    async def update(
        self,
        id: int,
        *,
        status: str | None = None,
        attempts: int | None = None,
        next_attempt_at: str | None = None,
        error: str | None = None,
    ) -> None:
        fields: dict = {}
        if status is not None:
            fields["status"] = status
        if attempts is not None:
            fields["attempts"] = attempts
        if next_attempt_at is not None:
            fields["next_attempt_at"] = next_attempt_at
        if error is not None:
            fields["error"] = error
        if not fields:
            return
        set_clause = ", ".join(f"{column} = ?" for column in fields)
        await self._conn.execute(
            f"UPDATE delivery_queue SET {set_clause} WHERE id = ?",
            (*fields.values(), id),
        )
        await self._conn.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> DeliveryTask:
        return DeliveryTask(
            id=row["id"],
            hit_id=row["hit_id"],
            receiver_chat_id=row["receiver_chat_id"],
            status=row["status"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            error=row["error"],
        )
