"""Доступ к панели (R59): только OWNER_ID; чужому — отказ + запись в лог, без
выполнения хендлера. Проверяется прямым вызовом middleware на фейковых
Update-подобных объектах — без реального Bot API (executor.md).
"""
from __future__ import annotations

import logging

import pytest

from panel.auth import DENIED_TEXT, OwnerAccessMiddleware

OWNER_ID = 111


class FakeUser:
    def __init__(self, id: int, username: str | None = None):
        self.id = id
        self.username = username


class FakeEvent:
    def __init__(self, user_id: int):
        self.from_user = FakeUser(user_id)
        self.answers: list[str] = []

    async def answer(self, text, *args, **kwargs):
        self.answers.append(text)


@pytest.mark.asyncio
async def test_owner_passes_through_to_handler():
    middleware = OwnerAccessMiddleware(owner_id=OWNER_ID)
    event = FakeEvent(OWNER_ID)
    called = {}

    async def handler(ev, data):
        called["ok"] = True
        return "handled"

    result = await middleware(handler, event, {})

    assert called == {"ok": True}
    assert result == "handled"
    assert event.answers == []


@pytest.mark.asyncio
async def test_stranger_is_denied_and_handler_never_runs():
    middleware = OwnerAccessMiddleware(owner_id=OWNER_ID)
    event = FakeEvent(999)
    called = {}

    async def handler(ev, data):
        called["ok"] = True
        return "handled"

    result = await middleware(handler, event, {})

    assert "ok" not in called
    assert result is None
    assert event.answers == [DENIED_TEXT]


@pytest.mark.asyncio
async def test_stranger_attempt_is_logged(caplog):
    middleware = OwnerAccessMiddleware(owner_id=OWNER_ID)
    event = FakeEvent(999)

    async def handler(ev, data):
        return None

    with caplog.at_level(logging.WARNING, logger="panel.auth"):
        await middleware(handler, event, {})

    assert any("999" in record.getMessage() for record in caplog.records)
