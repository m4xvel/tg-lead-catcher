"""Тикет 05: статус/статистика/топ/история и диалог догона (R49-R53) через
`Dispatcher.feed_update`, без реального Telegram (см. промпт исполнителя).
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText
from aiogram.types import Chat, Message, User

import panel
from panel import keyboards as kb
from panel import stats as statsmod
from panel.auth import OwnerAccessMiddleware

from tests.panel.conftest import (
    FakeDialog,
    FakeSupergroup,
    OWNER_ID,
    build_callback_update,
    build_message_update,
    make_bot,
)

_ids = itertools.count(70_000)


class _TrackingTelethonClient:
    """Как `FakeTelethonClient`, но считает вызовы `iter_messages` по чату (для
    проверки, что «Да» на диалог догона реально запускает скан по источникам)."""

    def __init__(self):
        self.scanned_chat_ids: list[int] = []

    async def iter_dialogs(self):
        return
        yield  # pragma: no cover

    async def iter_messages(self, chat_id, **kwargs):
        self.scanned_chat_ids.append(chat_id)
        return
        yield  # pragma: no cover

    async def get_me(self):
        class _Me:
            id = 999

        return _Me()


def _msg(text):
    return Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Влад"),
        text=text,
    )


def _callback(data):
    from aiogram.types import CallbackQuery

    return CallbackQuery(
        id=str(next(_ids)),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Влад"),
        chat_instance="ci",
        data=data,
        message=_msg("x"),
    )


@pytest.fixture
def dp():
    d = Dispatcher(storage=MemoryStorage())
    router = statsmod.build_router()
    router.message.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    router.callback_query.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    d.include_router(router)
    return d


@pytest.fixture
def bot():
    return make_bot()


async def _feed(dp, bot, update, repos, tg_client=None):
    kwargs = dict(repos)
    kwargs["tg_client"] = tg_client
    await dp.feed_update(bot, update, **kwargs)


async def _add_hit(hits, *, source_chat_id, message_id, matched_keywords, created_at):
    await hits.add(
        source_chat_id=source_chat_id,
        message_id=message_id,
        matched_keywords=matched_keywords,
        text_hash=f"h{message_id}",
        text_preview="текст",
        created_at=created_at,
    )


def _dt(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


# --- format_pause_duration ------------------------------------------------


def test_format_pause_duration_matches_brief_example():
    assert statsmod.format_pause_duration(3 * 3600 + 20 * 60) == "3 ч 20 мин"
    assert statsmod.format_pause_duration(45 * 60) == "45 мин"
    assert statsmod.format_pause_duration(2 * 3600) == "2 ч"


# --- R50: 7 vs 30 дней дают разные числа при разном распределении --------


def test_period_stats_differ_between_7_and_30_days():
    hits = [
        _FakeHit(matched_keywords=["ремонт"], source_chat_id=-1, created_at=_dt(2)),
        _FakeHit(matched_keywords=["ремонт"], source_chat_id=-1, created_at=_dt(20)),
    ]
    stats_7 = statsmod.compute_period_stats(hits, 7)
    stats_30 = statsmod.compute_period_stats(hits, 30)
    assert stats_7.total == 1
    assert stats_30.total == 2


class _FakeHit:
    def __init__(self, *, matched_keywords, source_chat_id, created_at):
        self.matched_keywords = matched_keywords
        self.source_chat_id = source_chat_id
        self.created_at = created_at


# --- R51: топ отсортирован по убыванию -------------------------------------


def test_top_keywords_sorted_descending():
    hits = [
        _FakeHit(matched_keywords=["а"], source_chat_id=1, created_at=_dt(0)),
        _FakeHit(matched_keywords=["б"], source_chat_id=1, created_at=_dt(0)),
        _FakeHit(matched_keywords=["б"], source_chat_id=1, created_at=_dt(0)),
        _FakeHit(matched_keywords=["в", "б"], source_chat_id=1, created_at=_dt(0)),
    ]
    stats = statsmod.compute_period_stats(hits, 30)
    top = statsmod.top_keywords(stats)
    assert top[0] == ("б", 3)
    counts = [count for _, count in top]
    assert counts == sorted(counts, reverse=True)


# --- R52: история <= 50, рабочая ссылка -------------------------------------


def test_history_link_is_well_formed():
    link = statsmod.build_message_link(-1001234567890, 555)
    assert link == "https://t.me/c/1234567890/555"


async def test_history_screen_caps_at_50(repos):
    for i in range(60):
        await _add_hit(
            repos["hits"], source_chat_id=-100111, message_id=i, matched_keywords=["ремонт"],
            created_at=_dt(0),
        )
    recent = await repos["hits"].list(limit=statsmod.HISTORY_LIMIT)
    assert len(recent) == 50
    text = statsmod.format_history(recent)
    assert text.count("https://t.me/c/") == 50


# --- R49: статус показывает все пункты одновременно -------------------------


async def test_status_screen_reports_all_fields(dp, bot, repos):
    await repos["sources"].add(-100111, "Чат", "supergroup")
    await repos["keywords"].add("word", "ремонт")
    await _add_hit(repos["hits"], source_chat_id=-100111, message_id=1, matched_keywords=["ремонт"], created_at=_dt(0))
    hit = (await repos["hits"].list())[0]
    task = await repos["delivery_queue"].add(hit.id, 555)

    text = statsmod.format_status(
        monitoring_paused=False, sources_count=1, keywords_count=1,
        pending_count=1, failed_count=0, last_hit_at=hit.created_at,
    )
    assert "Источников: 1" in text
    assert "Ключей: 1" in text
    assert "ожидают 1" in text
    assert hit.created_at in text
    assert "работает" in text
    assert "▶️ Мониторинг работает" in text
    assert "⏸ Мониторинг работает" not in text

    paused_text = statsmod.format_status(
        monitoring_paused=True, sources_count=0, keywords_count=0,
        pending_count=0, failed_count=0, last_hit_at=None,
    )
    assert "остановлен" in paused_text
    assert "ещё не было" in paused_text
    assert "⏸ Мониторинг остановлен" in paused_text
    assert "▶️ Мониторинг остановлен" not in paused_text


# --- R53: диалог догона при снятии глобальной паузы --------------------------


async def test_short_pause_resumes_without_dialog(dp, bot, repos):
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos)
    assert (await repos["settings"].get("monitoring_paused")) == "0"


async def test_long_pause_asks_question_with_duration(dp, bot, repos):
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(hours=3, minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
    )
    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos)
    # пауза ещё не снята — ждём ответа на вопрос
    assert (await repos["settings"].get("monitoring_paused")) == "1"


async def test_resume_confirm_yes_scans_active_sources_and_unpauses(dp, bot, repos):
    tg_client = _TrackingTelethonClient()
    await repos["sources"].add(-100111, "Активный", "supergroup")
    paused_src = await repos["sources"].add(-100222, "На паузе", "supergroup")
    await repos["sources"].update(paused_src.id, paused=True)
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.ResumeConfirmCB(answer="yes").pack())),
        repos, tg_client,
    )

    assert (await repos["settings"].get("monitoring_paused")) == "0"
    assert tg_client.scanned_chat_ids == [-100111]  # только активный, не на паузе


async def test_resume_confirm_yes_warns_when_pause_exceeds_scan_days(dp, bot, repos):
    """Ревью R53: пауза длилась дольше `settings.scan_days` (по умолчанию 3 дня) —
    предохранитель `userbot_catchup.scan_source` (R37) мог не долистать до начала
    паузы, поэтому ответ обязан честно об этом предупредить."""
    tg_client = _TrackingTelethonClient()
    await repos["sources"].add(-100111, "Активный", "supergroup")
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.ResumeConfirmCB(answer="yes").pack())),
        repos, tg_client,
    )

    edits = [c for c in bot.session.calls if isinstance(c, EditMessageText)]
    assert "⚠️" in edits[-1].text
    assert "3" in edits[-1].text  # scan_days по умолчанию


async def test_resume_confirm_yes_no_warning_when_pause_within_scan_days(dp, bot, repos):
    """Пауза короче предохранителя — догон полный, предупреждать не о чем."""
    tg_client = _TrackingTelethonClient()
    await repos["sources"].add(-100111, "Активный", "supergroup")
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.ResumeConfirmCB(answer="yes").pack())),
        repos, tg_client,
    )

    edits = [c for c in bot.session.calls if isinstance(c, EditMessageText)]
    assert "⚠️" not in edits[-1].text


# --- R48/R64: ▶️ в panel.stats тоже не маскирует мёртвую сессию ------------


async def test_toggle_on_does_not_clear_session_dead_pause(dp, bot, repos):
    """Тот же хендлер, что и в `panel.sources` (см. тикет 04), но в
    `panel.stats.on_toggle_monitoring` — именно он реально подключается первым
    (тикет 07), так что защита от `session_dead` должна дублироваться и здесь:
    ▶️ не сбрасывает `monitoring_paused_reason` и не включает мониторинг."""
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set("monitoring_paused_reason", "session_dead")

    # кнопка на клавиатуре при paused=True — это BTN_PAUSE_OFF («▶️ Запустить мониторинг»)
    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos)

    assert (await repos["settings"].get("monitoring_paused")) == "1"
    assert (await repos["settings"].get("monitoring_paused_reason")) == "session_dead"


async def test_resume_confirm_no_unpauses_without_scanning(dp, bot, repos):
    tg_client = _TrackingTelethonClient()
    await repos["sources"].add(-100111, "Активный", "supergroup")
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set(
        "monitoring_paused_at",
        (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.ResumeConfirmCB(answer="no").pack())),
        repos, tg_client,
    )

    assert (await repos["settings"].get("monitoring_paused")) == "0"
    assert tg_client.scanned_chat_ids == []
