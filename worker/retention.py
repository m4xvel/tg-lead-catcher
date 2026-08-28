"""Суточная чистка старых срабатываний (R68): `hits` старше `retention_days`
вместе со связанными `delivery_queue`.

`HitsRepo`/`DeliveryQueueRepo` (store, тикет 01) не выставляют массовое удаление
по возрасту — ни один репозиторий не даёт `remove по условию`, только `remove(id)`.
Здесь используется тот же `aiosqlite.Connection`, что и у репозиториев, напрямую —
осознанное отступление от «SQL только внутри store», единственный способ реализовать
R68, не трогая store/.
"""
from __future__ import annotations

from datetime import datetime, timezone


async def purge_expired(conn, retention_days: int, *, now: datetime | None = None) -> int:
    """Удаляет `hits` старше `retention_days` и их `delivery_queue`. Возвращает число удалённых hits."""
    now = now or datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    cur = await conn.execute(
        "SELECT id FROM hits WHERE created_at < datetime(?, '-' || ? || ' days')",
        (now_str, retention_days),
    )
    rows = await cur.fetchall()
    ids = [row["id"] for row in rows]
    if not ids:
        return 0

    placeholders = ", ".join("?" for _ in ids)
    await conn.execute(f"DELETE FROM delivery_queue WHERE hit_id IN ({placeholders})", ids)
    await conn.execute(f"DELETE FROM hits WHERE id IN ({placeholders})", ids)
    await conn.commit()
    return len(ids)


async def run_daily(conn, *, retention_days_getter, sleep, interval_seconds: float = 86400.0) -> None:
    """Фоновый цикл: раз в сутки читает `settings.retention_days` и чистит (внешний `run()` не тестируется здесь)."""
    while True:
        retention_days = await retention_days_getter()
        await purge_expired(conn, retention_days)
        await sleep(interval_seconds)
