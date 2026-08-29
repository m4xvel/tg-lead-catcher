"""Тикет 09: раздел «⚙️ Настройки» — редактирование шести уже существующих
ключей `settings` (R26/R31/R33/R37/R62/R68) через `Dispatcher.feed_update`, без
реального Telegram (см. промпт исполнителя). Тестируем только швы, названные
в тикете: валидацию числового ввода, предохранитель `HARD_MSG_CAP` (R37),
тумблеры, round-trip через `SettingsRepo` и влияние нового шаблона на
`userbot.deliver.build_card` (R31).
"""
from __future__ import annotations

import itertools

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

import store
from panel import keyboards as kb
from panel import settings as settingsmod
from panel.auth import OwnerAccessMiddleware
from userbot import deliver as userbot_deliver
from userbot.catchup import HARD_MSG_CAP

from tests.panel.conftest import OWNER_ID, build_callback_update, build_message_update, make_bot

_ids = itertools.count(90_000)


def _msg(text: str) -> Message:
    return Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Влад"),
        text=text,
    )


def _callback(data: str) -> CallbackQuery:
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
    router = settingsmod.build_router()
    router.message.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    router.callback_query.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    d.include_router(router)
    return d


@pytest.fixture
def bot():
    return make_bot()


async def _feed(dp, bot, update, repos):
    await dp.feed_update(bot, update, **dict(repos))


# --- validate_int (R33/R37/R62/R68): нечисловой/отрицательный/нулевой ввод --


@pytest.mark.parametrize("raw", ["abc", "", "три", "1.5"])
def test_validate_int_rejects_non_numeric(raw):
    value, error = settingsmod.validate_int(raw, "retention_days")
    assert value is None
    assert "целое число" in error


@pytest.mark.parametrize("raw", ["-1", "-100", "0"])
def test_validate_int_rejects_zero_and_negative(raw):
    value, error = settingsmod.validate_int(raw, "rate_limit_per_min")
    assert value is None
    assert "больше нуля" in error


def test_validate_int_accepts_positive_number():
    value, error = settingsmod.validate_int("14", "dedup_window_days")
    assert value == 14
    assert error is None


# --- R37: scan_msgs зажат HARD_MSG_CAP ---------------------------------------


def test_validate_int_rejects_scan_msgs_above_hard_cap():
    value, error = settingsmod.validate_int(str(HARD_MSG_CAP + 1), "scan_msgs")
    assert value is None
    assert str(HARD_MSG_CAP) in error


def test_validate_int_accepts_scan_msgs_at_hard_cap():
    value, error = settingsmod.validate_int(str(HARD_MSG_CAP), "scan_msgs")
    assert value == HARD_MSG_CAP
    assert error is None


def test_validate_int_does_not_cap_other_numeric_fields_at_500():
    """Предохранитель специфичен для `scan_msgs` — `rate_limit_per_min` (R62)
    не должен молча отклонять значения выше 500."""
    value, error = settingsmod.validate_int("600", "rate_limit_per_min")
    assert value == 600
    assert error is None


# --- главное меню: пункт виден, ведёт в список из шести пунктов -------------


def test_settings_button_is_in_main_menu():
    markup = kb.main_menu_kb(monitoring_paused=False)
    texts = {btn.text for row in markup.keyboard for btn in row}
    assert kb.BTN_SETTINGS in texts


def test_settings_menu_shows_all_six_items_with_current_values():
    values = {
        "monitor_all_dm": "1",
        "card_template": "шаблон",
        "dedup_window_days": "7",
        "dedup_enabled": "0",
        "scan_days": "3",
        "scan_msgs": "500",
        "rate_limit_per_min": "20",
        "retention_days": "90",
    }
    markup = kb.settings_menu_kb(values)
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Личные чаты: вкл" in t for t in texts)
    assert any("Шаблон подписи" in t for t in texts)
    assert any("Окно дедупа: 7" in t for t in texts)
    assert any("Дедуп" in t and "выкл" in t for t in texts)
    assert any("Глубина догона (дни): 3" in t for t in texts)
    assert any("Глубина догона (сообщения): 500" in t for t in texts)
    assert any("Лимит отправки: 20" in t for t in texts)
    assert any("Ретеншн: 90" in t for t in texts)


async def test_settings_menu_command_renders_current_db_values(dp, bot, repos):
    await repos["settings"].update("scan_days", "5")
    await _feed(dp, bot, build_message_update(_msg(kb.BTN_SETTINGS)), repos)
    from aiogram.methods import SendMessage

    sent = [c for c in bot.session.calls if isinstance(c, SendMessage)]
    assert sent
    texts = [btn.text for row in sent[-1].reply_markup.inline_keyboard for btn in row]
    assert any("Глубина догона (дни): 5" in t for t in texts)


# --- тумблеры переключаются одним нажатием -----------------------------------


async def test_toggle_monitor_all_dm_flips_value(dp, bot, repos):
    assert (await repos["settings"].get("monitor_all_dm")) == "0"
    cb = kb.SettingsToggleCB(key="monitor_all_dm").pack()
    await _feed(dp, bot, build_callback_update(_callback(cb)), repos)
    assert (await repos["settings"].get("monitor_all_dm")) == "1"
    await _feed(dp, bot, build_callback_update(_callback(cb)), repos)
    assert (await repos["settings"].get("monitor_all_dm")) == "0"


async def test_toggle_dedup_enabled_flips_value(dp, bot, repos):
    assert (await repos["settings"].get("dedup_enabled")) == "1"
    cb = kb.SettingsToggleCB(key="dedup_enabled").pack()
    await _feed(dp, bot, build_callback_update(_callback(cb)), repos)
    assert (await repos["settings"].get("dedup_enabled")) == "0"


# --- показывает текущее значение перед вводом нового -------------------------


async def test_edit_start_shows_current_value_before_prompting(dp, bot, repos):
    await repos["settings"].update("retention_days", "42")
    cb = kb.SettingsEditCB(key="retention_days").pack()
    await _feed(dp, bot, build_callback_update(_callback(cb)), repos)
    from aiogram.methods import EditMessageText

    edits = [c for c in bot.session.calls if isinstance(c, EditMessageText)]
    assert "42" in edits[-1].text


async def test_edit_start_shows_current_card_template(dp, bot, repos):
    cb = kb.SettingsEditCB(key="card_template").pack()
    await _feed(dp, bot, build_callback_update(_callback(cb)), repos)
    from aiogram.methods import EditMessageText

    edits = [c for c in bot.session.calls if isinstance(c, EditMessageText)]
    current_template = await repos["settings"].get("card_template")
    assert current_template in edits[-1].text
    assert "{keywords}" in edits[-1].text


# --- числовой ввод: ошибка на бабл ввод, не падение --------------------------


async def test_numeric_input_rejects_bad_value_with_russian_error(dp, bot, repos):
    await _feed(
        dp, bot, build_callback_update(_callback(kb.SettingsEditCB(key="retention_days").pack())), repos
    )
    await _feed(dp, bot, build_message_update(_msg("не число")), repos)
    from aiogram.methods import SendMessage

    sent = [c for c in bot.session.calls if isinstance(c, SendMessage) and c.text]
    assert any("целое число" in c.text for c in sent)
    # значение в БД не поменялось (осталось дефолтным)
    assert (await repos["settings"].get("retention_days")) == "90"


async def test_numeric_input_saves_valid_value_and_confirms(dp, bot, repos):
    await _feed(
        dp, bot, build_callback_update(_callback(kb.SettingsEditCB(key="retention_days").pack())), repos
    )
    await _feed(dp, bot, build_message_update(_msg("14")), repos)
    from aiogram.methods import SendMessage

    sent = [c for c in bot.session.calls if isinstance(c, SendMessage) and c.text]
    assert any("✅ Сохранено: 14" in c.text for c in sent)
    assert (await repos["settings"].get("retention_days")) == "14"


# --- round-trip: все изменения читаются обратно из SettingsRepo -------------


async def test_all_six_settings_round_trip_through_repo(dp, bot, repos):
    numeric_new_values = {
        "dedup_window_days": "10",
        "scan_days": "2",
        "scan_msgs": "300",
        "rate_limit_per_min": "30",
        "retention_days": "60",
    }
    for key, new_value in numeric_new_values.items():
        await _feed(
            dp, bot, build_callback_update(_callback(kb.SettingsEditCB(key=key).pack())), repos
        )
        await _feed(dp, bot, build_message_update(_msg(new_value)), repos)
        assert (await repos["settings"].get(key)) == new_value

    await _feed(
        dp, bot, build_callback_update(_callback(kb.SettingsEditCB(key="card_template").pack())), repos
    )
    await _feed(dp, bot, build_message_update(_msg("новый {keywords}")), repos)
    assert (await repos["settings"].get("card_template")) == "новый {keywords}"

    for key in ("monitor_all_dm", "dedup_enabled"):
        before = await repos["settings"].get(key)
        await _feed(
            dp, bot, build_callback_update(_callback(kb.SettingsToggleCB(key=key).pack())), repos
        )
        after = await repos["settings"].get(key)
        assert after != before


# --- R31: новый шаблон реально влияет на следующую собранную карточку -------


async def test_new_card_template_affects_next_build_card(dp, bot, repos):
    source = await repos["sources"].add(-100111, "Тестовый чат", "supergroup")
    hit = await repos["hits"].add(
        source_chat_id=source.chat_id,
        message_id=1,
        matched_keywords=["ремонт"],
        text_hash="h1",
        text_preview="текст",
        author_id=42,
        author_username="ivan",
        author_name="Иван",
        created_at="2026-01-01 12:00:00",
    )

    await _feed(
        dp, bot, build_callback_update(_callback(kb.SettingsEditCB(key="card_template").pack())), repos
    )
    await _feed(dp, bot, build_message_update(_msg("Только ключ: {keywords}")), repos)

    saved_template = await repos["settings"].get("card_template")
    card = userbot_deliver.build_card(
        saved_template, hit=hit, source=source, link="https://t.me/c/111/1"
    )
    assert card == "Только ключ: ремонт"
