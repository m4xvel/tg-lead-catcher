"""Тикет 07: сборка процесса `panel.main` — единый роутер панели через
`panel.build_router` в правильном порядке (R53 обязан перехватывать тумблер
паузы раньше R48, см. `panel/__init__.py` и «Из тикета 05» в interfaces.md) и
`panel.main.build`/`run` (DI-зависимости, что до блокирующего `start_polling`
всё остальное отработало) — без реального Bot API/Telegram (см. промпт
исполнителя).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, User

import panel
import store
from panel import keyboards as kb
from panel import main as panel_main

from tests.panel.conftest import FakeTelethonClient, OWNER_ID, build_message_update, make_bot


def _msg(text: str) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Влад"),
        text=text,
    )


def _sent_texts(bot) -> list[str]:
    return [m.text for m in bot.session.calls if isinstance(m, SendMessage) and m.text]


async def test_combined_router_prefers_stats_toggle_over_sources_toggle_for_r53(repos):
    """Критично для тикета 07: `panel.build_router()` обязан подключать
    `panel.stats` раньше `panel.sources` — оба реализуют `on_toggle_monitoring`
    на одну кнопку; если бы победил `sources`, пауза снялась бы мгновенно, без
    вопроса о догоне."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    bot = make_bot()
    tg_client = FakeTelethonClient()

    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    kwargs = dict(repos)
    kwargs["tg_client"] = tg_client
    await dp.feed_update(bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), **kwargs)

    # Если бы сработал sources.on_toggle_monitoring, пауза снялась бы сразу же.
    assert (await repos["settings"].get("monitoring_paused")) == "1"
    assert any("Догнать пропущенное" in t for t in _sent_texts(bot))


async def test_build_wires_repos_router_and_answers_owner_start(monkeypatch, repos):
    monkeypatch.setenv("OWNER_ID", str(OWNER_ID))
    tg_client = FakeTelethonClient()
    bot = make_bot()

    dp, got_bot, built_repos, got_tg_client = await panel_main.build(
        db_path=":memory:", bot=bot, tg_client=tg_client
    )

    assert got_bot is bot
    assert got_tg_client is tg_client
    assert set(built_repos) == {
        "sources", "receivers", "keywords", "hits", "delivery_queue", "settings",
    }

    kwargs = dict(built_repos)
    kwargs["tg_client"] = got_tg_client
    await dp.feed_update(bot, build_message_update(_msg("/start")), **kwargs)

    assert _sent_texts(bot), "владелец должен получить ответ на /start"


async def test_build_uses_separate_panel_session_not_shared_with_userbot(monkeypatch):
    """Блокирующий пробел из ревью: panel и userbot — два разных процесса,
    делящих ./data/, но НЕ должны делить один файл сессии Telethon (гонка/
    повреждение SQLite при одновременном доступе). Проверяем, что при
    `tg_client=None` panel строит клиент со своим `session_path`, отдельным
    от `./data/userbot`, которым пользуется userbot (`userbot/client.py`)."""
    monkeypatch.setenv("OWNER_ID", str(OWNER_ID))
    captured: dict = {}

    class FakeClient:
        async def start(self):
            captured["started"] = True

    def fake_build_client(session_path=None):
        captured["session_path"] = session_path
        return FakeClient()

    monkeypatch.setattr(panel_main, "build_client", fake_build_client)

    dp, bot, repos, tg_client = await panel_main.build(db_path=":memory:", bot=make_bot())

    assert captured.get("started") is True
    session_path = captured["session_path"]
    assert session_path is not None
    userbot_session_path = str(panel_main.DATA_DIR / "userbot")
    assert session_path != userbot_session_path
    assert session_path == str(panel_main.DATA_DIR / panel_main.PANEL_SESSION_NAME)


async def test_run_builds_then_blocks_on_start_polling_with_wired_kwargs(monkeypatch):
    monkeypatch.setenv("OWNER_ID", str(OWNER_ID))
    tg_client = FakeTelethonClient()
    bot = make_bot()
    captured: dict = {}

    async def fake_start_polling(self, bot_arg, **kwargs):
        captured["bot"] = bot_arg
        captured["kwargs"] = kwargs

    monkeypatch.setattr(Dispatcher, "start_polling", fake_start_polling)

    await panel_main.run(db_path=":memory:", bot=bot, tg_client=tg_client)

    assert captured["bot"] is bot
    assert captured["kwargs"]["tg_client"] is tg_client
    assert "sources" in captured["kwargs"]
    assert "settings" in captured["kwargs"]
