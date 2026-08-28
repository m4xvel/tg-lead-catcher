from __future__ import annotations

import pytest

import matcher
import store


@pytest.fixture
async def conn():
    connection = await store.connect(":memory:")
    yield connection
    await connection.close()


@pytest.fixture
def repos(conn):
    return {
        "sources": store.SourcesRepo(conn),
        "receivers": store.ReceiversRepo(conn),
        "hits": store.HitsRepo(conn),
        "delivery_queue": store.DeliveryQueueRepo(conn),
        "settings": store.SettingsRepo(conn),
    }


@pytest.fixture
def ruleset():
    return matcher.compile([("word", "ремонт")], [])
