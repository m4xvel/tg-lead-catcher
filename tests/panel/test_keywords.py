"""Тикет 05: ключи/минус-слова через `Dispatcher.feed_update` (без реального
Telegram, см. промпт исполнителя) — критерии приёмки R44-R47/R79i.
"""
from __future__ import annotations

import itertools

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import panel
from panel import keywords as kwmod
from panel import keyboards as kb
from panel.auth import OwnerAccessMiddleware

from tests.panel.conftest import OWNER_ID, build_callback_update, build_message_update, make_bot

_ids = itertools.count(50_000)


def _msg(text):
    from aiogram.types import Chat, Message, User

    return Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Влад"),
        text=text,
    )


def _callback(data):
    from aiogram.types import CallbackQuery, User

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
    router = kwmod.build_router()
    router.message.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    router.callback_query.outer_middleware(OwnerAccessMiddleware(OWNER_ID))
    d.include_router(router)
    return d


@pytest.fixture
def bot():
    return make_bot()


async def _feed(dp, bot, update, repos):
    await dp.feed_update(bot, update, **repos)


# --- parse_keyword_line -------------------------------------------------


def test_parse_keyword_line_word_phrase_regex():
    assert kwmod.parse_keyword_line("ремонт") == ("word", "ремонт")
    assert kwmod.parse_keyword_line('"ищу подрядчика"') == ("phrase", "ищу подрядчика")
    assert kwmod.parse_keyword_line(r"re:\d{3}-\d{2}") == ("regex", r"\d{3}-\d{2}")
    assert kwmod.parse_keyword_line("   ") is None


# --- R44: построчный ввод, до N новых, дубли молча ----------------------


async def test_bulk_add_ten_lines_adds_ten_and_repeat_line_is_silent_dup(repos):
    text = "\n".join(f"ключ{i}" for i in range(10))
    result = await kwmod.add_keyword_lines(text, repos["keywords"], is_stop=False)
    assert len(result.added) == 10

    # тот же текст ещё раз (и повтор внутри самого текста) — дублей не создаёт
    again = await kwmod.add_keyword_lines("ключ0\nключ0\nключ1", repos["keywords"], is_stop=False)
    assert again.added == []
    all_keywords = await repos["keywords"].list(is_stop=False)
    assert len(all_keywords) == 10


async def test_bulk_add_case_and_yo_insensitive_dedup(repos):
    await kwmod.add_keyword_lines("Ремонт", repos["keywords"], is_stop=False)
    result = await kwmod.add_keyword_lines("ремонт\nремОнт", repos["keywords"], is_stop=False)
    assert result.added == []


# --- R45: минус-слова в своём разделе тем же вводом ----------------------


async def test_bulk_add_reports_invalid_regex_but_keeps_valid_lines(repos):
    result = await kwmod.add_keyword_lines("ремонт\nre:[unclosed", repos["keywords"], is_stop=False)
    assert len(result.added) == 1
    assert result.added[0].pattern == "ремонт"
    assert len(result.errors) == 1
    kws = await repos["keywords"].list(is_stop=False)
    assert len(kws) == 1


async def test_stopwords_bulk_add_separate_from_keywords(repos):
    await kwmod.add_keyword_lines("ремонт\nсрочно", repos["keywords"], is_stop=False)
    await kwmod.add_keyword_lines("вакансия", repos["keywords"], is_stop=True)

    kws = await repos["keywords"].list(is_stop=False)
    stops = await repos["keywords"].list(is_stop=True)
    assert {k.pattern for k in kws} == {"ремонт", "срочно"}
    assert {s.pattern for s in stops} == {"вакансия"}


# --- R47: «Проверить» — три точных текста --------------------------------


async def test_check_matched_lists_exact_keywords(repos):
    await repos["keywords"].add("word", "ремонт")
    await repos["keywords"].add("word", "срочно")
    ruleset = await kwmod.build_ruleset(repos["keywords"])
    result = ruleset.match("нужен ремонт срочно")
    assert kwmod.format_check_result(result) == "✅ сработало по: ремонт, срочно"


async def test_check_not_matched(repos):
    await repos["keywords"].add("word", "ремонт")
    ruleset = await kwmod.build_ruleset(repos["keywords"])
    result = ruleset.match("что-то другое")
    assert kwmod.format_check_result(result) == "❌ не сработало"


async def test_check_blocked_by_stopword_when_both_present(repos):
    await repos["keywords"].add("word", "ремонт")
    await repos["keywords"].add("word", "вакансия", is_stop=True)
    ruleset = await kwmod.build_ruleset(repos["keywords"])
    result = ruleset.match("ремонт, есть вакансия")
    assert kwmod.format_check_result(result) == "⛔ заблокировано минус-словом: вакансия"


# --- R79i: ❌ без подтверждения, сквозь Dispatcher ------------------------


async def test_delete_keyword_removes_immediately_without_confirmation(dp, bot, repos):
    added = await repos["keywords"].add("word", "ремонт")
    update = build_callback_update(_callback(kb.DeleteCB(target=kb.TARGET_KEYWORD, id=added.id).pack()))
    await _feed(dp, bot, update, repos)
    assert await repos["keywords"].list(is_stop=False) == []


async def test_stranger_cannot_delete_keyword(dp, bot, repos):
    from aiogram.types import CallbackQuery, User

    added = await repos["keywords"].add("word", "ремонт")
    stranger_cb = CallbackQuery(
        id=str(next(_ids)),
        from_user=User(id=42, is_bot=False, first_name="Чужой"),
        chat_instance="ci",
        data=kb.DeleteCB(target=kb.TARGET_KEYWORD, id=added.id).pack(),
        message=_msg("x"),
    )
    await _feed(dp, bot, build_callback_update(stranger_cb), repos)
    assert len(await repos["keywords"].list(is_stop=False)) == 1


# --- сквозной сценарий: команда меню -> добавление -> список --------------


async def test_bulk_add_end_to_end_via_dispatcher(dp, bot, repos):
    await _feed(dp, bot, build_message_update(_msg(kb.BTN_KEYWORDS)), repos)
    await _feed(
        dp,
        bot,
        build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_KEYWORD).pack())),
        repos,
    )
    await _feed(dp, bot, build_message_update(_msg("ремонт\nсрочно\nремонт")), repos)

    kws = await repos["keywords"].list(is_stop=False)
    assert {k.pattern for k in kws} == {"ремонт", "срочно"}
