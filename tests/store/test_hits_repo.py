"""Дедуп по id (R32) и по автору+тексту за окно (R33) — на реальной SQLite, без сети."""
from datetime import datetime, timedelta, timezone

import pytest

import store


@pytest.fixture
async def conn():
    connection = await store.connect(":memory:")
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_duplicate_source_and_message_id_is_rejected(conn):
    repo = store.HitsRepo(conn)
    await repo.add(
        source_chat_id=100,
        message_id=555,
        author_id=1,
        matched_keywords=["ремонт"],
        text_hash="hash-a",
        text_preview="нужен ремонт",
    )

    with pytest.raises(store.DuplicateHitError):
        await repo.add(
            source_chat_id=100,
            message_id=555,
            author_id=1,
            matched_keywords=["ремонт"],
            text_hash="hash-a",
            text_preview="нужен ремонт (повтор)",
        )

    assert len(await repo.list()) == 1


@pytest.mark.asyncio
async def test_duplicate_by_author_and_text_within_window(conn):
    repo = store.HitsRepo(conn)
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    await repo.add(
        source_chat_id=100,
        message_id=1,
        author_id=42,
        matched_keywords=["ремонт"],
        text_hash="same-text-hash",
        text_preview="нужен ремонт",
        created_at=recent,
    )

    is_dup = await repo.is_duplicate_by_author_text(
        author_id=42, text_hash="same-text-hash", window_days=7
    )
    assert is_dup is True

    # другой автор/текст — не дубль
    assert (
        await repo.is_duplicate_by_author_text(
            author_id=99, text_hash="same-text-hash", window_days=7
        )
        is False
    )


@pytest.mark.asyncio
async def test_duplicate_by_author_text_respects_window_and_toggle(conn):
    repo = store.HitsRepo(conn)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    await repo.add(
        source_chat_id=200,
        message_id=2,
        author_id=42,
        matched_keywords=["ремонт"],
        text_hash="old-hash",
        text_preview="нужен ремонт",
        created_at=old,
    )

    # за пределами окна в 7 дней — не дубль
    assert (
        await repo.is_duplicate_by_author_text(
            author_id=42, text_hash="old-hash", window_days=7
        )
        is False
    )

    # dedup_enabled=False — дубли не ищем вовсе, даже если внутри окна
    assert (
        await repo.is_duplicate_by_author_text(
            author_id=42, text_hash="old-hash", window_days=30, dedup_enabled=False
        )
        is False
    )


@pytest.mark.asyncio
async def test_update_changes_fields(conn):
    repo = store.HitsRepo(conn)
    hit = await repo.add(
        source_chat_id=300,
        message_id=1,
        author_id=1,
        matched_keywords=["ремонт"],
        text_hash="hash-b",
        text_preview="нужен ремонт",
    )

    await repo.update(hit.id, matched_keywords=["ремонт", "срочно"], also_in=[999])

    updated = await repo.get(hit.id)
    assert updated.matched_keywords == ["ремонт", "срочно"]
    assert updated.also_in == [999]


@pytest.mark.asyncio
async def test_remove_deletes_hit(conn):
    repo = store.HitsRepo(conn)
    hit = await repo.add(
        source_chat_id=301,
        message_id=2,
        author_id=1,
        matched_keywords=["ремонт"],
        text_hash="hash-c",
        text_preview="нужен ремонт",
    )

    await repo.remove(hit.id)

    assert await repo.get(hit.id) is None
