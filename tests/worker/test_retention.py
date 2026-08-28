"""Суточная чистка (R68): hits старше retention_days удаляются вместе с delivery_queue."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import store
from worker.retention import purge_expired


def _dt(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


async def test_purge_removes_old_hits_and_their_delivery_queue(conn):
    hits_repo = store.HitsRepo(conn)
    queue_repo = store.DeliveryQueueRepo(conn)

    old_hit = await hits_repo.add(
        source_chat_id=1, message_id=1, matched_keywords=["ремонт"],
        text_hash="h1", text_preview="старое", created_at=_dt(days_ago=100),
    )
    new_hit = await hits_repo.add(
        source_chat_id=1, message_id=2, matched_keywords=["ремонт"],
        text_hash="h2", text_preview="свежее", created_at=_dt(days_ago=1),
    )
    old_task = await queue_repo.add(old_hit.id, receiver_chat_id=999)
    new_task = await queue_repo.add(new_hit.id, receiver_chat_id=999)

    deleted = await purge_expired(conn, retention_days=90)

    assert deleted == 1
    assert await hits_repo.get(old_hit.id) is None
    assert await hits_repo.get(new_hit.id) is not None
    assert await queue_repo.get(old_task.id) is None
    assert await queue_repo.get(new_task.id) is not None
