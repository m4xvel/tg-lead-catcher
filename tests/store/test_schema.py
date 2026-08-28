"""Схема SQLite создаётся с нуля, все шесть таблиц из interfaces.md существуют."""
import pytest

import store


@pytest.mark.asyncio
async def test_schema_creates_all_six_tables():
    conn = await store.connect(":memory:")
    try:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        rows = await cur.fetchall()
        names = {row["name"] for row in rows}

        for table in ("sources", "receivers", "keywords", "settings", "hits", "delivery_queue"):
            assert table in names
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_schema_is_idempotent():
    conn = await store.connect(":memory:")
    try:
        # повторное применение схемы к уже созданной БД не должно падать
        await store.create_schema(conn)
    finally:
        await conn.close()
