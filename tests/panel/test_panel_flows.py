"""Сквозные тесты панели через `Dispatcher.feed_update` с сфабрикованными
Update-объектами (без реального Telegram, см. промпт исполнителя) — критерии
приёмки тикета 04: доступ (R59), «Из моих чатов» (R38), форвард (R39), ручной
ввод (R40/R40.1), «Избранное» по умолчанию (R42), ❌ без подтверждения (R79i),
пауза источника → догон (R78i), глобальный тумблер (R48).
"""
from __future__ import annotations

import itertools

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, GetChat, SendMessage
from aiogram.types import (
    Chat,
    Message,
    MessageOriginChannel,
    User,
)
from telethon.errors import ChannelPrivateError

import panel
import store
from panel import keyboards as kb
from panel.sources import AddChatStates

from tests.panel.conftest import (
    FakeChannel,
    FakeDialog,
    FakeSupergroup,
    FakeTelethonClient,
    OWNER_ID,
    build_callback_update,
    build_message_update,
    make_bot,
)

_ids = itertools.count(10_000)


def _msg(text=None, user_id=OWNER_ID, chat_id=OWNER_ID, forward_origin=None, message_id=None):
    return Message(
        message_id=message_id or next(_ids),
        date=0,
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Влад"),
        text=text,
        forward_origin=forward_origin,
    )


def _callback(data, user_id=OWNER_ID, chat_id=OWNER_ID):
    from aiogram.types import CallbackQuery

    return CallbackQuery(
        id=str(next(_ids)),
        from_user=User(id=user_id, is_bot=False, first_name="Влад"),
        chat_instance="ci",
        data=data,
        message=_msg(chat_id=chat_id),
    )


@pytest.fixture
def dp():
    return Dispatcher(storage=MemoryStorage())


@pytest.fixture
def bot():
    return make_bot()


async def _feed(dp, bot, update, repos, tg_client, extra=None):
    kwargs = dict(repos)
    kwargs["tg_client"] = tg_client
    if extra:
        kwargs.update(extra)
    await dp.feed_update(bot, update, **kwargs)


async def _fsm_state(dp, bot, chat_id=OWNER_ID):
    key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=chat_id)
    return await FSMContext(storage=dp.storage, key=key).get_state()


# --- R59: доступ -------------------------------------------------------------


async def test_stranger_update_is_denied_and_nothing_added(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    update = build_message_update(_msg("/start", user_id=42))
    await _feed(dp, bot, update, repos, tg_client)

    assert await repos["sources"].list() == []


# --- R38: «Из моих чатов» — пагинация и поиск --------------------------------


async def test_from_my_chats_paginates_by_eight_and_search_narrows(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    dialogs = [
        FakeDialog(chat_id=-1000 - i, name=f"Чат {i}", entity=FakeSupergroup())
        for i in range(10)
    ]
    dialogs[3].name = "Ремонт окон"
    tg_client = FakeTelethonClient(dialogs=dialogs)

    # начинаем добавление источника методом "из моих чатов"
    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="dialogs").pack())
        ),
        repos,
        tg_client,
    )

    # поиск по названию сужает список до одного совпадения
    await _feed(
        dp,
        bot,
        build_callback_update(_callback(kb.DialogSearchCB(target=kb.TARGET_SOURCE).pack())),
        repos,
        tg_client,
    )
    await _feed(dp, bot, build_message_update(_msg("Ремонт")), repos, tg_client)

    # диалог, отфильтрованный поиском (не "Ремонт окон"), больше не в закешированной
    # странице — попытка выбрать его ничего не добавляет: список реально сузился,
    # а не просто отрисовался с прежним полным набором
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.DialogPickCB(target=kb.TARGET_SOURCE, chat_id=dialogs[0].id).pack())
        ),
        repos,
        tg_client,
    )
    assert await repos["sources"].list() == []

    # а найденный поиском диалог добавляется как источник
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.DialogPickCB(target=kb.TARGET_SOURCE, chat_id=dialogs[3].id).pack())
        ),
        repos,
        tg_client,
    )

    added = await repos["sources"].list()
    assert len(added) == 1
    assert added[0].chat_id == dialogs[3].id
    assert added[0].title == "Ремонт окон"
    assert added[0].kind == "supergroup"


# --- R39: форвард --------------------------------------------------------------


async def test_forwarded_message_adds_candidate_source(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="forward").pack())
        ),
        repos,
        tg_client,
    )

    origin = MessageOriginChannel(date=0, chat=Chat(id=-1005555, type="channel", title="Канал лидов"), message_id=1)
    await _feed(dp, bot, build_message_update(_msg(forward_origin=origin)), repos, tg_client)

    added = await repos["sources"].list()
    assert len(added) == 1
    assert added[0].chat_id == -1005555
    assert added[0].kind == "channel"


# --- R40/R40.1: ручной ввод ----------------------------------------------------


async def test_manual_entry_unresolvable_shows_error_and_adds_nothing(dp, repos):
    bot = make_bot(
        error=TelegramBadRequest(method=GetChat(chat_id="@no_such_chat"), message="chat not found")
    )
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_RECEIVER).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_RECEIVER, method="manual").pack())
        ),
        repos,
        tg_client,
    )
    await _feed(dp, bot, build_message_update(_msg("@no_such_chat")), repos, tg_client)

    assert await repos["receivers"].list() == []


async def test_manual_entry_success_adds_receiver(dp, repos):
    chat = Chat(id=777, type="group", title="Моя группа лидов")
    bot = make_bot(chats={"@leadgroup": chat})
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_RECEIVER).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_RECEIVER, method="manual").pack())
        ),
        repos,
        tg_client,
    )
    await _feed(dp, bot, build_message_update(_msg("@leadgroup")), repos, tg_client)

    added = await repos["receivers"].list()
    assert len(added) == 1
    assert added[0].chat_id == 777
    assert added[0].title == "Моя группа лидов"


# --- R42: «Избранное» ----------------------------------------------------------


async def test_favorite_added_in_one_tap(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient(me_id=424242)

    await _feed(dp, bot, build_callback_update(_callback(kb.FavoriteAddCB().pack())), repos, tg_client)

    added = await repos["receivers"].list()
    assert len(added) == 1
    assert added[0].chat_id == 424242


async def test_favorite_button_adds_favorite_when_other_receivers_already_exist(dp, bot, repos):
    """Явное нажатие «💾 Избранное» — не автозасев: должно реально добавлять
    «Избранное», даже когда список приёмников уже не пуст (баг ревью тикета 04:
    `on_add_favorite` переиспользовал guard «только если список пуст» и молча
    ничего не делал, но всё равно отвечал «добавлено»)."""
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient(me_id=424242)
    await repos["receivers"].add(-777, "Другой приёмник")

    update = build_callback_update(_callback(kb.FavoriteAddCB().pack()))
    await _feed(dp, bot, update, repos, tg_client)

    added = await repos["receivers"].list()
    assert {r.chat_id for r in added} == {-777, 424242}

    # первый AnswerCallbackQuery — явный ответ `on_add_favorite`; второй — молчаливый
    # ack от `render_list` (без текста), поэтому берём именно первый
    answers = [m for m in bot.session.calls if isinstance(m, AnswerCallbackQuery)]
    assert answers[0].text == "Избранное добавлено ✅"


async def test_favorite_button_second_tap_reports_already_present_without_duplicating(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient(me_id=424242)

    first = build_callback_update(_callback(kb.FavoriteAddCB().pack()))
    await _feed(dp, bot, first, repos, tg_client)
    assert len(await repos["receivers"].list()) == 1

    second = build_callback_update(_callback(kb.FavoriteAddCB().pack()))
    await _feed(dp, bot, second, repos, tg_client)

    added = await repos["receivers"].list()
    assert len(added) == 1  # не задублировалось

    # берём последний по счёту AnswerCallbackQuery *с текстом* — как и в первом
    # тесте, `render_list` следом шлёт свой собственный ack без текста
    answers = [m for m in bot.session.calls if isinstance(m, AnswerCallbackQuery) and m.text]
    assert answers[-1].text == "Избранное уже в приёмниках"


async def test_favorite_is_default_receiver_right_after_first_start(repos):
    """`router.startup` засевает «Избранное», если список приёмников ещё пуст."""
    from panel.receivers import on_startup

    tg_client = FakeTelethonClient(me_id=13)
    assert await repos["receivers"].list() == []

    await on_startup(receivers=repos["receivers"], tg_client=tg_client)

    added = await repos["receivers"].list()
    assert len(added) == 1
    assert added[0].chat_id == 13


# --- R38/R39/R40: дубликат чата отвечает честно, не «Добавлено ✅» -------------
# (ревью третьего круга доводки: DuplicateChatError раньше глотался молча, а
# бот всё равно отвечал «Добавлено ✅», хотя ничего не добавилось — как уже
# честно обрабатывает `panel.receivers.on_add_favorite` для «Избранного».)


async def test_dialog_pick_duplicate_reports_already_in_list_without_duplicating(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    await repos["sources"].add(-1000, "Уже источник", "supergroup")
    dialogs = [FakeDialog(chat_id=-1000, name="Уже источник", entity=FakeSupergroup())]
    tg_client = FakeTelethonClient(dialogs=dialogs)

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="dialogs").pack())),
        repos, tg_client,
    )
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.DialogPickCB(target=kb.TARGET_SOURCE, chat_id=-1000).pack())),
        repos, tg_client,
    )

    assert len(await repos["sources"].list()) == 1  # не задублировалось
    answers = [m for m in bot.session.calls if isinstance(m, AnswerCallbackQuery) and m.text]
    assert answers[-1].text == "Этот чат уже в списке"


async def test_manual_entry_duplicate_reports_already_in_list_without_duplicating(dp, repos):
    chat = Chat(id=777, type="group", title="Моя группа лидов")
    bot = make_bot(chats={"@leadgroup": chat})
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    await repos["receivers"].add(777, "Моя группа лидов")

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_RECEIVER).pack())), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.AddMethodCB(target=kb.TARGET_RECEIVER, method="manual").pack())),
        repos, tg_client,
    )
    await _feed(dp, bot, build_message_update(_msg("@leadgroup")), repos, tg_client)

    added = await repos["receivers"].list()
    assert len(added) == 1  # не задублировалось
    texts = [m.text for m in bot.session.calls if isinstance(m, SendMessage)]
    assert texts[0] == "Этот чат уже в списке"  # второй SendMessage — это уже render_list


async def test_forward_duplicate_reports_already_in_list_without_duplicating(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    await repos["sources"].add(-1005555, "Канал лидов", "channel")

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp, bot,
        build_callback_update(_callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="forward").pack())),
        repos, tg_client,
    )
    origin = MessageOriginChannel(date=0, chat=Chat(id=-1005555, type="channel", title="Канал лидов"), message_id=1)
    await _feed(dp, bot, build_message_update(_msg(forward_origin=origin)), repos, tg_client)

    added = await repos["sources"].list()
    assert len(added) == 1  # не задублировалось
    texts = [m.text for m in bot.session.calls if isinstance(m, SendMessage)]
    assert texts[0] == "Этот чат уже в списке"  # второй SendMessage — это уже render_list


# --- R79i: удаление без подтверждения ------------------------------------------


async def test_delete_removes_immediately_without_confirmation(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    source = await repos["sources"].add(-999, "Чат на удаление", "group")

    await _feed(
        dp,
        bot,
        build_callback_update(_callback(kb.DeleteCB(target=kb.TARGET_SOURCE, id=source.id).pack())),
        repos,
        tg_client,
    )

    assert await repos["sources"].list() == []


# --- R41/R78i: пауза источника + автодогон -------------------------------------


async def test_pausing_and_unpausing_source_triggers_catch_up(dp, bot, repos, monkeypatch):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    source = await repos["sources"].add(-321, "Чат под паузой", "group")

    calls = []

    async def fake_scan_source(client, src, **kwargs):
        calls.append(src.id)
        return None

    monkeypatch.setattr("panel.sources.userbot_catchup.scan_source", fake_scan_source)

    # ставим на паузу — скан не должен запускаться
    await _feed(dp, bot, build_callback_update(_callback(kb.PauseToggleCB(id=source.id).pack())), repos, tg_client)
    paused_source = await repos["sources"].get(id=source.id)
    assert paused_source.paused is True
    assert calls == []

    # снимаем паузу — без диалога сразу запускается scan_source (R78i)
    await _feed(dp, bot, build_callback_update(_callback(kb.PauseToggleCB(id=source.id).pack())), repos, tg_client)
    resumed_source = await repos["sources"].get(id=source.id)
    assert resumed_source.paused is False
    assert calls == [source.id]

    # реплика про конкретный источник (R41), а не про глобальный тумблер
    # мониторинга (R48) — те же слова про «мониторинг» в разных местах путают
    # два разных понятия (ревью третьего круга доводки)
    answers = [m for m in bot.session.calls if isinstance(m, AnswerCallbackQuery) and m.text]
    assert answers[-1].text == "Источник снят с паузы, догоняю пропущенное…"
    assert "Мониторинг" not in answers[-1].text


# --- R36: разовое сканирование истории за 7 дней по кнопке ---------------------


async def test_scan_button_triggers_seven_day_scan_of_that_source(dp, bot, repos, monkeypatch):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    source = await repos["sources"].add(-654, "Чат для скана", "group")
    other = await repos["sources"].add(-655, "Другой чат", "group")

    calls = []

    async def fake_scan_source(client, src, *, limit_days=None, **kwargs):
        calls.append((src.id, limit_days))
        return None

    monkeypatch.setattr("panel.sources.userbot_catchup.scan_source", fake_scan_source)

    await _feed(
        dp,
        bot,
        build_callback_update(_callback(kb.ScanCB(id=source.id).pack())),
        repos,
        tg_client,
    )

    assert calls == [(source.id, 7)]  # именно этот источник, именно 7 дней
    assert other.id not in [c[0] for c in calls]


# --- R48: глобальный тумблер независим от пауз источников ----------------------


async def test_global_toggle_is_independent_from_source_pause(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    source = await repos["sources"].add(-1, "Источник", "group")
    await repos["sources"].update(source.id, paused=True)

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_ON)), repos, tg_client)

    assert (await repos["settings"].get("monitoring_paused")) == "1"
    still_paused_source = await repos["sources"].get(id=source.id)
    assert still_paused_source.paused is True  # тумблер не трогает индивидуальную паузу

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)
    assert (await repos["settings"].get("monitoring_paused")) == "0"


# --- R48/R64: ▶️ не маскирует мёртвую сессию ------------------------------------


async def test_toggle_on_does_not_clear_session_dead_pause(dp, bot, repos):
    """Сессия Telethon мертва (worker/runner.py выставил session_dead) — ▶️ не
    сбрасывает `monitoring_paused_reason` и не включает мониторинг: пауза
    держится до повторного логина через `setup.py`, а не до нажатия кнопки."""
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    await repos["settings"].set("monitoring_paused", "1")
    await repos["settings"].set("monitoring_paused_reason", "session_dead")

    # кнопка на клавиатуре при paused=True — это BTN_PAUSE_OFF («▶️ Запустить мониторинг»)
    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)

    assert (await repos["settings"].get("monitoring_paused")) == "1"
    assert (await repos["settings"].get("monitoring_paused_reason")) == "session_dead"


async def test_toggle_on_still_works_for_ordinary_manual_pause(dp, bot, repos):
    """Контрольный случай: обычная ручная пауза (без session_dead) по-прежнему
    свободно снимается ▶️, как и раньше."""
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    await repos["settings"].set("monitoring_paused", "1")

    await _feed(dp, bot, build_message_update(_msg(kb.BTN_PAUSE_OFF)), repos, tg_client)

    assert (await repos["settings"].get("monitoring_paused")) == "0"
    assert (await repos["settings"].get("monitoring_paused_reason", "")) == ""


# --- R76i: scan_source на недоступном источнике не роняет хендлер --------------


async def test_unpause_scan_source_inaccessible_pauses_source_and_does_not_crash(
    dp, bot, repos, monkeypatch
):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    source = await repos["sources"].add(-777, "Кикнутый чат", "group")
    await repos["sources"].update(source.id, paused=True)

    async def fake_scan_source(client, src, **kwargs):
        raise ChannelPrivateError(request=None)

    monkeypatch.setattr("panel.sources.userbot_catchup.scan_source", fake_scan_source)

    # снятие паузы вызывает scan_source, который падает недоступностью источника —
    # хендлер не должен упасть, источник должен остаться (снова) на паузе
    await _feed(
        dp, bot, build_callback_update(_callback(kb.PauseToggleCB(id=source.id).pack())), repos, tg_client
    )

    refreshed = await repos["sources"].get(id=source.id)
    assert refreshed.paused is True


async def test_scan_now_inaccessible_source_pauses_and_does_not_crash(dp, bot, repos, monkeypatch):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()
    source = await repos["sources"].add(-778, "Удалённый чат", "group")

    async def fake_scan_source(client, src, *, limit_days=None, **kwargs):
        raise ChannelPrivateError(request=None)

    monkeypatch.setattr("panel.sources.userbot_catchup.scan_source", fake_scan_source)

    await _feed(
        dp, bot, build_callback_update(_callback(kb.ScanCB(id=source.id).pack())), repos, tg_client
    )

    refreshed = await repos["sources"].get(id=source.id)
    assert refreshed.paused is True


# --- тикет 11 (G01): команда во время ожидания ввода добавления чата = --------
# тихая отмена, ничего не добавляется (тот же вид, что и кнопка «‹ Отмена»)


async def test_command_during_manual_entry_is_silently_cancelled(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_RECEIVER).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_RECEIVER, method="manual").pack())
        ),
        repos,
        tg_client,
    )
    await _feed(dp, bot, build_message_update(_msg("/start")), repos, tg_client)

    assert await repos["receivers"].list() == []
    assert (await _fsm_state(dp, bot)) is None


async def test_command_during_forward_wait_is_silently_cancelled(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="forward").pack())
        ),
        repos,
        tg_client,
    )
    # /export нарочно не берём: `panel.export.cmd_export` (Command("export"),
    # роутер подключён раньше `sources`) перехватил бы его первым — это другой,
    # реальный командный обработчик, не тот случай, что чинит этот тикет.
    await _feed(dp, bot, build_message_update(_msg("/foo")), repos, tg_client)

    assert await repos["sources"].list() == []
    assert (await _fsm_state(dp, bot)) is None


async def test_command_during_dialog_search_is_silently_cancelled(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    dialogs = [FakeDialog(chat_id=-2000, name="Чат", entity=FakeSupergroup())]
    tg_client = FakeTelethonClient(dialogs=dialogs)

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="dialogs").pack())
        ),
        repos,
        tg_client,
    )
    await _feed(
        dp,
        bot,
        build_callback_update(_callback(kb.DialogSearchCB(target=kb.TARGET_SOURCE).pack())),
        repos,
        tg_client,
    )
    await _feed(dp, bot, build_message_update(_msg("/foo")), repos, tg_client)

    assert await repos["sources"].list() == []
    assert (await _fsm_state(dp, bot)) is None


# --- тикет 11 (G02): существующие потоки добавления чата (kb.cancel_kb) -------
# продолжают работать как раньше (проверка неломки, не только команда-отмена)


async def test_manual_entry_cancel_button_still_returns_to_list(dp, bot, repos):
    dp.include_router(panel.build_router(owner_id=OWNER_ID))
    tg_client = FakeTelethonClient()

    await _feed(dp, bot, build_callback_update(_callback(kb.AddStartCB(target=kb.TARGET_SOURCE).pack())), repos, tg_client)
    await _feed(
        dp,
        bot,
        build_callback_update(
            _callback(kb.AddMethodCB(target=kb.TARGET_SOURCE, method="manual").pack())
        ),
        repos,
        tg_client,
    )
    await _feed(
        dp,
        bot,
        build_callback_update(_callback(kb.CancelAddCB(target=kb.TARGET_SOURCE).pack())),
        repos,
        tg_client,
    )

    assert await repos["sources"].list() == []
    assert (await _fsm_state(dp, bot)) is None
