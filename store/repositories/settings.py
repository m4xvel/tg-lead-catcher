"""Репозиторий настроек — плоский key/value (значения по умолчанию сеются в db.create_schema)."""
from __future__ import annotations

import aiosqlite


class SettingsRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, key: str, default: str | None = None) -> str | None:
        cur = await self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn.commit()

    async def list(self) -> dict[str, str]:
        cur = await self._conn.execute("SELECT key, value FROM settings")
        rows = await cur.fetchall()
        return {row["key"]: row["value"] for row in rows}

    async def add(self, key: str, value: str) -> None:
        """Заводит новую настройку. Ключ уже занят — падает как обычный конфликт PK."""
        await self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        await self._conn.commit()

    async def update(self, key: str, value: str) -> None:
        """Меняет значение существующей настройки (нет строки — просто ничего не делает)."""
        await self._conn.execute(
            "UPDATE settings SET value = ? WHERE key = ?", (value, key)
        )
        await self._conn.commit()

    async def remove(self, key: str) -> None:
        await self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        await self._conn.commit()
