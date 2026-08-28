"""Кривой regex отклоняется при добавлении (R19.1) и не попадает в список ключей."""
import pytest

import matcher
import store


@pytest.fixture
async def conn():
    connection = await store.connect(":memory:")
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_valid_keyword_is_added(conn):
    repo = store.KeywordsRepo(conn)
    added = await repo.add("word", "ремонт")

    assert added.pattern == "ремонт"
    assert [k.pattern for k in await repo.list()] == ["ремонт"]


@pytest.mark.asyncio
async def test_invalid_regex_is_rejected_and_not_stored(conn):
    repo = store.KeywordsRepo(conn)

    with pytest.raises(matcher.InvalidKeywordError):
        await repo.add("regex", "(")

    assert await repo.list() == []
