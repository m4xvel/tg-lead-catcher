"""Тикет 07: сборка процесса `userbot.main` — порядок вызовов при старте
(клиент подключается -> догоняет пропущенное -> регистрирует live-хендлеры ->
запускает цикл доставки -> слушает), без реального Telegram (см. промпт
исполнителя). Догон/матчинг/доставка — зона тикетов 02/03, здесь не
перепроверяются, только то, что `userbot.main` их действительно вызывает и в
правильном порядке (R06/R11/R65).
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import store
import worker
from userbot import main as userbot_main


class FakeClient:
    """Минимум интерфейса Telethon-клиента, которым пользуется `userbot.main`:
    подключение, регистрация хендлеров, бесконечное ожидание отключения."""

    def __init__(self, calls: list) -> None:
        self.calls = calls

    async def start(self) -> None:
        self.calls.append("client.start")

    def add_event_handler(self, callback, event_builder=None) -> None:
        self.calls.append("add_event_handler")

    async def run_until_disconnected(self) -> None:
        self.calls.append("run_until_disconnected")


async def test_run_wires_catchup_listeners_and_worker_in_correct_order(monkeypatch):
    calls: list = []
    client = FakeClient(calls)

    async def fake_run_startup_catchup(client_arg, **kwargs):
        assert client_arg is client
        calls.append("run_startup_catchup")
        return {}

    def fake_worker_run(conn, client_arg, **kwargs):
        assert client_arg is client
        calls.append("worker.run")

        async def _noop() -> None:
            return None

        return _noop()

    def fake_run_daily(conn, **kwargs):
        calls.append("retention.run_daily")

        async def _noop() -> None:
            return None

        return _noop()

    monkeypatch.setattr(userbot_main, "run_startup_catchup", fake_run_startup_catchup)
    monkeypatch.setattr(worker, "run", fake_worker_run)
    monkeypatch.setattr(worker.retention, "run_daily", fake_run_daily)

    await userbot_main.run(db_path=":memory:", client=client)

    assert calls == [
        "client.start",
        "run_startup_catchup",
        "add_event_handler",  # NewMessage
        "add_event_handler",  # MessageEdited
        "add_event_handler",  # Album
        "worker.run",
        "retention.run_daily",
        "run_until_disconnected",
    ]


async def test_run_builds_client_when_not_injected(monkeypatch):
    """`client=None` (реальный запуск) должен пойти через `userbot.build_client()`,
    не падать на попытке использовать не переданный клиент."""
    calls: list = []
    client = FakeClient(calls)

    monkeypatch.setattr(userbot_main, "build_client", lambda: client)

    async def fake_run_startup_catchup(client_arg, **kwargs):
        calls.append("run_startup_catchup")
        return {}

    def fake_worker_run(conn, client_arg, **kwargs):
        calls.append("worker.run")

        async def _noop() -> None:
            return None

        return _noop()

    monkeypatch.setattr(userbot_main, "run_startup_catchup", fake_run_startup_catchup)
    monkeypatch.setattr(worker, "run", fake_worker_run)

    await userbot_main.run(db_path=":memory:")

    assert "client.start" in calls
    assert "run_until_disconnected" in calls


# --------------------------------------------------------------------------
# Блокирующий пробел из ревью: фоновые задачи не должны теряться молча
# (голый asyncio.create_task без сохранённой ссылки — GC может собрать задачу
# на середине выполнения, без ошибки и следа; см. докстринг userbot/main.py).
# --------------------------------------------------------------------------


async def test_spawn_tracked_task_keeps_strong_reference_until_done():
    background_tasks: set = set()

    async def _never_finishes():
        await asyncio.Event().wait()

    task = userbot_main._spawn_tracked_task(background_tasks, _never_finishes(), "probe")

    # Пока задача жива — ссылка держится в background_tasks (не только в
    # цикле событий как слабая ссылка), иначе GC мог бы её собрать.
    assert task in background_tasks

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)  # даём done_callback отработать

    assert task not in background_tasks


async def test_failed_background_task_is_logged_not_lost_silently(monkeypatch, caplog):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_ID", raising=False)
    background_tasks: set = set()

    async def _boom():
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.ERROR, logger="userbot.main"):
        task = userbot_main._spawn_tracked_task(background_tasks, _boom(), "probe-fail")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)  # даём done_callback (логирование) отработать

    assert task not in background_tasks
    assert any(
        "probe-fail" in record.getMessage() for record in caplog.records
    ), "падение фоновой задачи должно попасть в лог, а не потеряться молча"


async def test_failed_background_task_notifies_owner_via_bot_api_when_configured(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:TEST-TOKEN")
    monkeypatch.setenv("OWNER_ID", "555")
    background_tasks: set = set()
    notify_calls = []

    async def fake_notify(bot_token, owner_id, text, **kwargs):
        notify_calls.append((bot_token, owner_id, text))
        return True

    monkeypatch.setattr(worker.bot_notify, "notify_owner_via_bot_api", fake_notify)

    async def _boom():
        raise RuntimeError("kaboom")

    task = userbot_main._spawn_tracked_task(background_tasks, _boom(), "probe-notify")
    with pytest.raises(RuntimeError):
        await task
    # done_callback планирует уведомление отдельной задачей — даём ей отработать.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(notify_calls) == 1
    assert notify_calls[0][0] == "123:TEST-TOKEN"
    assert notify_calls[0][1] == 555
    assert "probe-notify" in notify_calls[0][2]


# --------------------------------------------------------------------------
# R14: TZ из окружения (пишет мастер настройки `configure.py` в .env) должен
# дойти до settings.tz при старте — иначе карточка доставки всегда использует
# сид-значение Europe/Moscow из схемы, а не то, что реально в .env.
# --------------------------------------------------------------------------


def _patch_background_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_startup_catchup(client_arg, **kwargs):
        return {}

    def fake_worker_run(conn, client_arg, **kwargs):
        async def _noop() -> None:
            return None

        return _noop()

    def fake_run_daily(conn, **kwargs):
        async def _noop() -> None:
            return None

        return _noop()

    monkeypatch.setattr(userbot_main, "run_startup_catchup", fake_run_startup_catchup)
    monkeypatch.setattr(worker, "run", fake_worker_run)
    monkeypatch.setattr(worker.retention, "run_daily", fake_run_daily)


async def test_run_syncs_settings_tz_from_env_when_different(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Yekaterinburg")
    _patch_background_loops(monkeypatch)
    db_path = str(tmp_path / "bot.db")
    client = FakeClient([])

    await userbot_main.run(db_path=db_path, client=client)

    conn = await store.connect(db_path)
    try:
        settings = store.SettingsRepo(conn)
        assert await settings.get("tz") == "Asia/Yekaterinburg"
    finally:
        await conn.close()


async def test_run_leaves_settings_tz_untouched_when_env_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    _patch_background_loops(monkeypatch)
    db_path = str(tmp_path / "bot.db")
    client = FakeClient([])

    await userbot_main.run(db_path=db_path, client=client)

    conn = await store.connect(db_path)
    try:
        settings = store.SettingsRepo(conn)
        assert await settings.get("tz") == "Europe/Moscow"
    finally:
        await conn.close()
