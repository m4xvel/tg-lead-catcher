"""Тесты SettingsRepo.add/remove/update — контракт репозитория (interfaces.md)."""
import pytest

import store


@pytest.fixture
async def conn():
    connection = await store.connect(":memory:")
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_add_then_get(conn):
    repo = store.SettingsRepo(conn)
    await repo.add("custom_flag", "on")

    assert await repo.get("custom_flag") == "on"


@pytest.mark.asyncio
async def test_update_changes_existing_value(conn):
    repo = store.SettingsRepo(conn)
    # dedup_window_days уже засеян значением по умолчанию (db.DEFAULT_SETTINGS)
    await repo.update("dedup_window_days", "14")

    assert await repo.get("dedup_window_days") == "14"


@pytest.mark.asyncio
async def test_remove_deletes_key(conn):
    repo = store.SettingsRepo(conn)
    await repo.add("custom_toggle", "0")

    await repo.remove("custom_toggle")

    assert await repo.get("custom_toggle") is None
    assert "custom_toggle" not in await repo.list()
