"""worker.process_pending — ретраи, лимит, FloodWait, смерть сессии (R43, R61-R64).

Реальная SQLite (:memory:, фикстура `conn`) + фейковый Telethon-клиент за швом
userbot.deliver — предпочтённые швы (interfaces.md), без новых моков внутренностей.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from telethon.errors import FloodWaitError
from telethon.errors.rpcerrorlist import AuthKeyUnregisteredError

import store
from worker.ratelimit import RateLimiter
from worker.runner import MAX_ATTEMPTS, process_pending


async def _make_hit_and_queue(conn, *, receiver_chat_id=-100999):
    sources = store.SourcesRepo(conn)
    receivers = store.ReceiversRepo(conn)
    hits = store.HitsRepo(conn)
    queue = store.DeliveryQueueRepo(conn)

    source = await sources.add(chat_id=-100111, title="Ремонт СПб", kind="supergroup")
    receiver = await receivers.add(chat_id=receiver_chat_id, title="Приёмник")
    hit = await hits.add(
        source_chat_id=source.chat_id, message_id=1, author_id=42,
        matched_keywords=["ремонт"], text_hash="h", text_preview="нужен ремонт",
    )
    task = await queue.add(hit.id, receiver_chat_id=receiver.chat_id)
    return source, receiver, hit, task


async def test_three_failures_mark_failed_and_notify_once_without_blocking_other_receiver(
    conn, fake_client
):
    source, receiver, hit, task = await _make_hit_and_queue(conn)
    # второй приёмник по тому же hit_id, всегда доставляется успешно
    ok_receiver = await store.ReceiversRepo(conn).add(chat_id=-100555, title="ОК-приёмник")
    ok_task = await store.DeliveryQueueRepo(conn).add(hit.id, receiver_chat_id=ok_receiver.chat_id)

    fake_client.forward_error_for[receiver.chat_id] = RuntimeError("сеть недоступна")
    limiter = RateLimiter(1000)
    now = datetime.now(timezone.utc)

    queue = store.DeliveryQueueRepo(conn)
    for attempt in range(MAX_ATTEMPTS):
        await process_pending(conn, fake_client, limiter=limiter, now=now)
        now += timedelta(minutes=10)  # заведомо дальше любой растущей задержки

    failed_task = await queue.get(task.id)
    assert failed_task.status == "failed"
    assert failed_task.attempts == MAX_ATTEMPTS

    notify_calls = [c for c in fake_client.sent_messages if c[0] == "me"]
    assert len(notify_calls) == 1

    # приёмник, доставляющий успешно, не заблокирован ошибками первого
    ok_result = await queue.get(ok_task.id)
    assert ok_result.status == "sent"


async def test_flood_wait_does_not_consume_attempt_and_sleeps_exact_seconds(
    conn, fake_client, caplog
):
    source, receiver, hit, task = await _make_hit_and_queue(conn)
    fake_client.forward_error = FloodWaitError(request=None, capture=17)
    limiter = RateLimiter(1000)

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    with caplog.at_level("WARNING"):
        await process_pending(conn, fake_client, limiter=limiter, sleep=fake_sleep)

    queue = store.DeliveryQueueRepo(conn)
    unchanged = await queue.get(task.id)
    assert unchanged.attempts == 0
    assert unchanged.status == "pending"
    assert 17 in sleeps
    assert any("17" in record.message for record in caplog.records)


async def test_auth_key_unregistered_notifies_owner_and_pauses_monitoring(conn, fake_client, monkeypatch):
    # R64: сессия умерла — уведомление обязано уйти через Bot API (отдельный канал,
    # переживающий смерть userbot-сессии), а НЕ через userbot.notify_owner (тот же
    # умерший клиент). См. worker/bot_notify.py.
    monkeypatch.setenv("BOT_TOKEN", "123:fake-bot-token")
    monkeypatch.setenv("OWNER_ID", "42")
    source, receiver, hit, task = await _make_hit_and_queue(conn)
    fake_client.forward_error = AuthKeyUnregisteredError(request=None)
    limiter = RateLimiter(1000)

    bot_api_calls: list[tuple[str, dict]] = []

    async def fake_http_post(url, payload):
        bot_api_calls.append((url, payload))
        return 200

    cycle = await process_pending(conn, fake_client, limiter=limiter, http_post=fake_http_post)

    assert cycle.session_dead is True
    settings = store.SettingsRepo(conn)
    assert await settings.get("monitoring_paused") == "1"
    assert await settings.get("monitoring_paused_reason") == "session_dead"

    # Умершая MTProto-сессия не участвует в уведомлении — никакого send_message("me", ...).
    notify_calls = [c for c in fake_client.sent_messages if c[0] == "me"]
    assert len(notify_calls) == 0

    assert len(bot_api_calls) == 1
    url, payload = bot_api_calls[0]
    assert url == "https://api.telegram.org/bot123:fake-bot-token/sendMessage"
    assert payload["chat_id"] == 42
    assert "Сессия отвалилась" in payload["text"]


async def test_auth_key_unregistered_bot_api_failure_still_pauses_and_does_not_crash(
    conn, fake_client, monkeypatch
):
    # Даже если сама попытка уведомить через Bot API падает (сеть недоступна и т.п.),
    # process_pending не должен падать — состояние уже зафиксировано в settings ДО
    # попытки уведомить (шаг 1 из фикса R64).
    monkeypatch.setenv("BOT_TOKEN", "123:fake-bot-token")
    monkeypatch.setenv("OWNER_ID", "42")
    source, receiver, hit, task = await _make_hit_and_queue(conn)
    fake_client.forward_error = AuthKeyUnregisteredError(request=None)
    limiter = RateLimiter(1000)

    async def failing_http_post(url, payload):
        raise RuntimeError("сеть недоступна")

    cycle = await process_pending(conn, fake_client, limiter=limiter, http_post=failing_http_post)

    assert cycle.session_dead is True
    settings = store.SettingsRepo(conn)
    assert await settings.get("monitoring_paused") == "1"
    assert await settings.get("monitoring_paused_reason") == "session_dead"
