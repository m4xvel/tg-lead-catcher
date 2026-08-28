"""Репозиторий ключей и минус-слов. Валидация regex делегирована matcher (R19.1)."""
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

import matcher


@dataclass(frozen=True)
class Keyword:
    id: int
    kind: str
    pattern: str
    is_stop: bool
    created_at: str


class KeywordsRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, kind: str, pattern: str, is_stop: bool = False) -> Keyword:
        # Бросает matcher.InvalidKeywordError с понятным текстом на кривом regex —
        # ключ не попадает в список (R19.1). Понимает те же word/phrase/regex, что и Ruleset.
        matcher.compile([(kind, pattern)], [])

        cur = await self._conn.execute(
            "INSERT INTO keywords (kind, pattern, is_stop) VALUES (?, ?, ?)",
            (kind, pattern, int(is_stop)),
        )
        await self._conn.commit()
        added = await self.get(cur.lastrowid)
        assert added is not None
        return added

    async def remove(self, id: int) -> None:
        await self._conn.execute("DELETE FROM keywords WHERE id = ?", (id,))
        await self._conn.commit()

    async def get(self, id: int) -> Keyword | None:
        cur = await self._conn.execute("SELECT * FROM keywords WHERE id = ?", (id,))
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def list(self, is_stop: bool | None = None) -> list[Keyword]:
        if is_stop is None:
            cur = await self._conn.execute("SELECT * FROM keywords ORDER BY id")
        else:
            cur = await self._conn.execute(
                "SELECT * FROM keywords WHERE is_stop = ? ORDER BY id", (int(is_stop),)
            )
        rows = await cur.fetchall()
        return [self._from_row(row) for row in rows]

    async def update(
        self, id: int, *, pattern: str | None = None, is_stop: bool | None = None
    ) -> None:
        fields: dict = {}
        if pattern is not None:
            fields["pattern"] = pattern
        if is_stop is not None:
            fields["is_stop"] = int(is_stop)
        if not fields:
            return
        set_clause = ", ".join(f"{column} = ?" for column in fields)
        await self._conn.execute(
            f"UPDATE keywords SET {set_clause} WHERE id = ?",
            (*fields.values(), id),
        )
        await self._conn.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> Keyword:
        return Keyword(
            id=row["id"],
            kind=row["kind"],
            pattern=row["pattern"],
            is_stop=bool(row["is_stop"]),
            created_at=row["created_at"],
        )
