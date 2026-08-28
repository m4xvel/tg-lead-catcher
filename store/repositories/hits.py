"""Репозиторий срабатываний: лог (R67), дедуп по id (R32) и по автору+тексту (R33)."""
from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite

from ..errors import DuplicateHitError

_PREVIEW_LIMIT = 500


@dataclass(frozen=True)
class Hit:
    id: int
    source_chat_id: int
    message_id: int
    author_id: int | None
    author_username: str | None
    author_name: str | None
    matched_keywords: list[str]
    text_hash: str
    text_preview: str
    also_in: list[int]
    created_at: str


class HitsRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(
        self,
        *,
        source_chat_id: int,
        message_id: int,
        matched_keywords: list[str],
        text_hash: str,
        text_preview: str,
        author_id: int | None = None,
        author_username: str | None = None,
        author_name: str | None = None,
        also_in: list[int] | None = None,
        created_at: str | None = None,
    ) -> Hit:
        columns = [
            "source_chat_id", "message_id", "author_id", "author_username",
            "author_name", "matched_keywords", "text_hash", "text_preview", "also_in",
        ]
        values: list = [
            source_chat_id,
            message_id,
            author_id,
            author_username,
            author_name,
            json.dumps(list(matched_keywords), ensure_ascii=False),
            text_hash,
            (text_preview or "")[:_PREVIEW_LIMIT],
            json.dumps(list(also_in or []), ensure_ascii=False),
        ]
        if created_at is not None:
            columns.append("created_at")
            values.append(created_at)

        placeholders = ", ".join("?" for _ in values)
        sql = f"INSERT INTO hits ({', '.join(columns)}) VALUES ({placeholders})"
        try:
            cur = await self._conn.execute(sql, values)
        except aiosqlite.IntegrityError as exc:
            raise DuplicateHitError(
                f"срабатывание chat_id={source_chat_id}, message_id={message_id} "
                "уже записано"
            ) from exc
        await self._conn.commit()
        hit = await self.get(cur.lastrowid)
        assert hit is not None
        return hit

    async def get(self, id: int) -> Hit | None:
        cur = await self._conn.execute("SELECT * FROM hits WHERE id = ?", (id,))
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def list(self, limit: int | None = None) -> list[Hit]:
        sql = "SELECT * FROM hits ORDER BY created_at DESC, id DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        return [self._from_row(row) for row in rows]

    async def remove(self, id: int) -> None:
        await self._conn.execute("DELETE FROM hits WHERE id = ?", (id,))
        await self._conn.commit()

    async def update(
        self,
        id: int,
        *,
        matched_keywords: list[str] | None = None,
        text_preview: str | None = None,
        also_in: list[int] | None = None,
    ) -> None:
        fields: dict = {}
        if matched_keywords is not None:
            fields["matched_keywords"] = json.dumps(list(matched_keywords), ensure_ascii=False)
        if text_preview is not None:
            fields["text_preview"] = text_preview[:_PREVIEW_LIMIT]
        if also_in is not None:
            fields["also_in"] = json.dumps(list(also_in), ensure_ascii=False)
        if not fields:
            return
        set_clause = ", ".join(f"{column} = ?" for column in fields)
        await self._conn.execute(
            f"UPDATE hits SET {set_clause} WHERE id = ?", (*fields.values(), id)
        )
        await self._conn.commit()

    async def update_also_in(self, id: int, also_in: list[int]) -> None:
        await self._conn.execute(
            "UPDATE hits SET also_in = ? WHERE id = ?",
            (json.dumps(list(also_in), ensure_ascii=False), id),
        )
        await self._conn.commit()

    async def is_duplicate_by_author_text(
        self,
        *,
        author_id: int | None,
        text_hash: str,
        window_days: int,
        dedup_enabled: bool = True,
        now: str | None = None,
    ) -> bool:
        """Тот же автор с тем же нормализованным текстом уже прилетал за окно N дней?"""
        if not dedup_enabled or author_id is None:
            return False

        now_expr = "?" if now is not None else "datetime('now')"
        params: list = [author_id, text_hash]
        if now is not None:
            params.append(now)
        params.append(window_days)

        sql = (
            "SELECT 1 FROM hits WHERE author_id = ? AND text_hash = ? "
            f"AND created_at >= datetime({now_expr}, '-' || ? || ' days') LIMIT 1"
        )
        cur = await self._conn.execute(sql, params)
        row = await cur.fetchone()
        return row is not None

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> Hit:
        return Hit(
            id=row["id"],
            source_chat_id=row["source_chat_id"],
            message_id=row["message_id"],
            author_id=row["author_id"],
            author_username=row["author_username"],
            author_name=row["author_name"],
            matched_keywords=json.loads(row["matched_keywords"]),
            text_hash=row["text_hash"],
            text_preview=row["text_preview"],
            also_in=json.loads(row["also_in"]) if row["also_in"] else [],
            created_at=row["created_at"],
        )
