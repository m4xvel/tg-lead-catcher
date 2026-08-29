"""Источники (R02, R36, R38-R41, R78i, R79i): список, добавление тремя способами,
❌ без подтверждения, пауза со снятием и разовое сканирование за 7 дней — оба
через `userbot.catchup.scan_source`.

`render_list`/`resolve_manual_chat`/`extract_forwarded_chat`/`AddChatStates` —
общий каркас, которым пользуется и `panel.receivers` (R42 — те же три способа
добавления плюс «Избранное»), поэтому здесь они публичные, не приватные.

`build_router()` — фабрика, а не модульный синглтон: `Router` в aiogram можно
подключить (`include_router`) только один раз, поэтому свежий объект нужен на
каждый вызов `panel.build_router()` (в т.ч. в тестах, где диспетчер строится
заново на каждый тест).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import matcher
import store
import userbot
from userbot import catchup as userbot_catchup
from worker.runner import SESSION_DEAD_TEXT

from . import keyboards as kb


class ChatResolutionError(Exception):
    """Понятная ошибка ручного/форвардного резолва чата (R40.1)."""


@dataclass(frozen=True)
class ResolvedChat:
    chat_id: int
    title: str
    kind: str


class AddChatStates(StatesGroup):
    choosing_method = State()
    waiting_manual = State()
    waiting_forward = State()
    browsing_dialogs = State()
    waiting_search = State()


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def parse_chat_reference(text: str) -> str | int:
    """Разбирает `@username` / `t.me/...` / числовой id (R40) в то, что понимает
    `Bot.get_chat`. Кривой формат — понятная ошибка, ничего не резолвится (R40.1).
    """
    text = (text or "").strip()

    m = re.match(r"^(?:https?://)?t\.me/c/(\d+)", text)
    if m:
        return int(f"-100{m.group(1)}")

    m = re.match(r"^(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})", text)
    if m:
        return f"@{m.group(1)}"

    if text.startswith("@") and _USERNAME_RE.match(text[1:]):
        return text

    if re.fullmatch(r"-?\d+", text):
        return int(text)

    raise ChatResolutionError(
        f"Не понимаю «{text}» — введите @username, ссылку t.me/... или числовой id."
    )


async def resolve_manual_chat(bot, text: str) -> ResolvedChat:
    """Резолвит ручной ввод (R40) через Bot API; понятная ошибка при неудаче (R40.1)."""
    ref = parse_chat_reference(text)
    try:
        chat = await bot.get_chat(ref)
    except TelegramAPIError as exc:
        raise ChatResolutionError(
            f"Не удалось найти чат «{text}»: бот до него не достучался."
        ) from exc
    title = chat.title or chat.username or getattr(chat, "first_name", None) or str(chat.id)
    return ResolvedChat(chat_id=chat.id, title=title, kind=chat.type)


def extract_forwarded_chat(message) -> ResolvedChat | None:
    """Достаёт чат-источник из пересланного сообщения (R39) через `forward_origin`."""
    origin = getattr(message, "forward_origin", None)
    if origin is None:
        return None

    kind = getattr(origin, "type", None)
    if kind == "channel":
        chat = origin.chat
        return ResolvedChat(chat_id=chat.id, title=chat.title or str(chat.id), kind="channel")
    if kind == "chat":
        chat = origin.sender_chat
        chat_kind = "supergroup" if getattr(chat, "type", "") == "supergroup" else "group"
        return ResolvedChat(chat_id=chat.id, title=chat.title or str(chat.id), kind=chat_kind)
    if kind == "user":
        user = origin.sender_user
        name = " ".join(filter(None, [user.first_name, user.last_name])) or (
            user.username or str(user.id)
        )
        return ResolvedChat(chat_id=user.id, title=name, kind="private")
    return None  # hidden_user — id недоступен, чат не определить


async def _reply(event: Union[Message, CallbackQuery], text: str, markup) -> None:
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


async def render_list(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
    *,
    target: str,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    offset: int = 0,
) -> None:
    """Постраничный список источников/приёмников (R41/R42) — общий для обоих."""
    await state.clear()
    repo = sources if target == kb.TARGET_SOURCE else receivers
    items = await repo.list()
    label = "Источники" if target == kb.TARGET_SOURCE else "Приёмники"
    text = f"{kb.section_emoji(target)} {label}:" if items else f"{label}: список пуст."
    markup = kb.list_page_kb(
        target,
        items,
        offset,
        with_pause=(target == kb.TARGET_SOURCE),
        show_favorite=(target == kb.TARGET_RECEIVER),
    )
    await _reply(event, text, markup)
    if isinstance(event, CallbackQuery):
        await event.answer()


async def _add_chat(
    target: str,
    chat_id: int,
    title: str,
    kind: str,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    try:
        if target == kb.TARGET_SOURCE:
            await sources.add(chat_id, title, kind)
        else:
            await receivers.add(chat_id, title)
    except store.DuplicateChatError:
        pass  # уже в списке — молча не дублируем


async def _build_ruleset_provider(keywords: store.KeywordsRepo):
    kws = await keywords.list(is_stop=False)
    stops = await keywords.list(is_stop=True)
    ruleset = matcher.compile(
        [(k.kind, k.pattern) for k in kws],
        [(k.kind, k.pattern) for k in stops],
    )
    return lambda: ruleset


async def _show_dialogs_page(
    event: Union[Message, CallbackQuery],
    state: FSMContext,
    *,
    target: str,
    tg_client,
    offset: int,
    query: str | None,
) -> None:
    page = await userbot.list_dialogs(tg_client, offset=offset, limit=kb.PAGE_SIZE, query=query)
    await state.update_data(
        target=target,
        dialogs_query=query,
        dialogs={str(d.chat_id): {"title": d.title, "kind": d.kind} for d in page},
    )
    has_more = len(page) >= kb.PAGE_SIZE
    text = "📋 Ваши чаты:" if page else "Ничего не найдено."
    markup = kb.dialogs_page_kb(target, page, offset, has_more=has_more)
    await _reply(event, text, markup)
    if isinstance(event, CallbackQuery):
        await event.answer()


# --- хендлеры (регистрируются фабрикой `build_router`, см. низ файла) --------


async def cmd_start(message: Message, settings: store.SettingsRepo) -> None:
    paused = (await settings.get("monitoring_paused", "0")) == "1"
    await message.answer(
        "👋 Панель tg-lead-catcher.", reply_markup=kb.main_menu_kb(monitoring_paused=paused)
    )


async def on_toggle_monitoring(message: Message, settings: store.SettingsRepo) -> None:
    """Глобальный тумблер мониторинга (R48) — отдельно от паузы источников.

    Сессия Telethon мертва (R64: `monitoring_paused_reason == "session_dead"`) —
    ▶️ не сбрасывает причину и не включает мониторинг: пауза держится до
    повторного логина через `setup.py`, а не до нажатия кнопки в панели.
    """
    was_paused = (await settings.get("monitoring_paused", "0")) == "1"
    reason = await settings.get("monitoring_paused_reason", "")
    if was_paused and reason == "session_dead":
        await message.answer(
            f"{SESSION_DEAD_TEXT} (`python configure.py`), потом включай мониторинг.",
            reply_markup=kb.main_menu_kb(monitoring_paused=True),
        )
        return
    new_paused = not was_paused
    await settings.set("monitoring_paused", "1" if new_paused else "0")
    if not new_paused:
        await settings.set("monitoring_paused_reason", "")
    text = "⏸ Мониторинг остановлен." if new_paused else "▶️ Мониторинг запущен."
    await message.answer(text, reply_markup=kb.main_menu_kb(monitoring_paused=new_paused))


async def cmd_sources_menu(
    message: Message,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    await render_list(message, state, target=kb.TARGET_SOURCE, sources=sources, receivers=receivers)


async def on_list_page(
    callback: CallbackQuery,
    callback_data: kb.ListPageCB,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    await render_list(
        callback,
        state,
        target=callback_data.target,
        sources=sources,
        receivers=receivers,
        offset=callback_data.offset,
    )


async def on_delete(
    callback: CallbackQuery,
    callback_data: kb.DeleteCB,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    """❌ удаляет немедленно, без подтверждающего диалога (R79i)."""
    repo = sources if callback_data.target == kb.TARGET_SOURCE else receivers
    await repo.remove(callback_data.id)
    await render_list(callback, state, target=callback_data.target, sources=sources, receivers=receivers)


async def on_pause_toggle(
    callback: CallbackQuery,
    callback_data: kb.PauseToggleCB,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    keywords: store.KeywordsRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    tg_client,
) -> None:
    """Пауза источника (R41); снятие без вопроса о догоне запускает скан (R78i)."""
    source = await sources.get(id=callback_data.id)
    if source is None:
        await callback.answer()
        return

    if source.paused:
        await sources.update(source.id, paused=False)
        refreshed = await sources.get(id=source.id)
        ruleset_provider = await _build_ruleset_provider(keywords)
        try:
            await userbot_catchup.scan_source(
                tg_client,
                refreshed,
                sources=sources,
                receivers=receivers,
                hits=hits,
                delivery_queue=delivery_queue,
                settings=settings,
                ruleset_provider=ruleset_provider,
            )
        except userbot_catchup.INACCESSIBLE_SOURCE_ERRORS:
            await sources.update(source.id, paused=True)
            await callback.answer("⚠️ Источник недоступен, поставлен на паузу")
        else:
            await callback.answer("Мониторинг возобновлён, догоняю пропущенное…")
    else:
        await sources.update(source.id, paused=True)
        await callback.answer("Источник на паузе")

    await render_list(callback, state, target=kb.TARGET_SOURCE, sources=sources, receivers=receivers)


async def on_scan_now(
    callback: CallbackQuery,
    callback_data: kb.ScanCB,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    keywords: store.KeywordsRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    tg_client,
) -> None:
    """Разовое сканирование истории источника за 7 дней по кнопке (R36)."""
    source = await sources.get(id=callback_data.id)
    if source is None:
        await callback.answer()
        return

    ruleset_provider = await _build_ruleset_provider(keywords)
    try:
        result = await userbot_catchup.scan_source(
            tg_client,
            source,
            sources=sources,
            receivers=receivers,
            hits=hits,
            delivery_queue=delivery_queue,
            settings=settings,
            ruleset_provider=ruleset_provider,
            limit_days=7,
        )
    except userbot_catchup.INACCESSIBLE_SOURCE_ERRORS:
        await sources.update(source.id, paused=True)
        await callback.answer("⚠️ Источник недоступен, поставлен на паузу")
        return
    text = "Ничего нового за 7 дней." if result is None else "Просканировано ✅"
    await callback.answer(text)


async def on_add_start(callback: CallbackQuery, callback_data: kb.AddStartCB, state: FSMContext) -> None:
    await state.set_state(AddChatStates.choosing_method)
    await state.update_data(target=callback_data.target)
    await callback.message.edit_text(
        "Как добавить чат?", reply_markup=kb.add_method_kb(callback_data.target)
    )
    await callback.answer()


async def on_cancel_add(
    callback: CallbackQuery,
    callback_data: kb.CancelAddCB,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    await render_list(callback, state, target=callback_data.target, sources=sources, receivers=receivers)


async def on_add_method(
    callback: CallbackQuery, callback_data: kb.AddMethodCB, state: FSMContext, tg_client
) -> None:
    target = callback_data.target
    if callback_data.method == "manual":
        await state.set_state(AddChatStates.waiting_manual)
        await callback.message.edit_text(
            "Пришлите @username, ссылку t.me/... или числовой id чата.",
            reply_markup=kb.cancel_kb(target),
        )
        await callback.answer()
    elif callback_data.method == "forward":
        await state.set_state(AddChatStates.waiting_forward)
        await callback.message.edit_text(
            "Перешлите сюда любое сообщение из нужного чата.", reply_markup=kb.cancel_kb(target)
        )
        await callback.answer()
    elif callback_data.method == "dialogs":
        await state.set_state(AddChatStates.browsing_dialogs)
        await _show_dialogs_page(callback, state, target=target, tg_client=tg_client, offset=0, query=None)


async def on_dialog_page(
    callback: CallbackQuery, callback_data: kb.DialogPageCB, state: FSMContext, tg_client
) -> None:
    data = await state.get_data()
    await _show_dialogs_page(
        callback,
        state,
        target=callback_data.target,
        tg_client=tg_client,
        offset=callback_data.offset,
        query=data.get("dialogs_query"),
    )


async def on_dialog_search_prompt(
    callback: CallbackQuery, callback_data: kb.DialogSearchCB, state: FSMContext
) -> None:
    await state.update_data(target=callback_data.target)
    await state.set_state(AddChatStates.waiting_search)
    await callback.message.edit_text(
        "Введите текст для поиска по названию чата.", reply_markup=kb.cancel_kb(callback_data.target)
    )
    await callback.answer()


async def on_dialog_search_text(message: Message, state: FSMContext, tg_client) -> None:
    data = await state.get_data()
    target = data.get("target", kb.TARGET_SOURCE)
    await state.set_state(AddChatStates.browsing_dialogs)
    await _show_dialogs_page(
        message, state, target=target, tg_client=tg_client, offset=0, query=message.text
    )


async def on_dialog_pick(
    callback: CallbackQuery,
    callback_data: kb.DialogPickCB,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    data = await state.get_data()
    info = (data.get("dialogs") or {}).get(str(callback_data.chat_id))
    if info is None:
        await callback.answer("Список устарел, откройте заново.", show_alert=True)
        return
    await _add_chat(
        callback_data.target, callback_data.chat_id, info["title"], info["kind"], sources, receivers
    )
    await callback.answer("Добавлено ✅")
    await render_list(callback, state, target=callback_data.target, sources=sources, receivers=receivers)


async def on_manual_text(
    message: Message, state: FSMContext, sources: store.SourcesRepo, receivers: store.ReceiversRepo
) -> None:
    data = await state.get_data()
    target = data.get("target", kb.TARGET_SOURCE)
    try:
        resolved = await resolve_manual_chat(message.bot, message.text or "")
    except ChatResolutionError as exc:
        await message.answer(
            f"❌ {exc}\nПопробуйте ещё раз или нажмите «{kb.BTN_CANCEL}».",
            reply_markup=kb.cancel_kb(target),
        )
        return
    await _add_chat(target, resolved.chat_id, resolved.title, resolved.kind, sources, receivers)
    await message.answer("Добавлено ✅")
    await render_list(message, state, target=target, sources=sources, receivers=receivers)


async def on_forward(
    message: Message, state: FSMContext, sources: store.SourcesRepo, receivers: store.ReceiversRepo
) -> None:
    data = await state.get_data()
    target = data.get("target", kb.TARGET_SOURCE)
    resolved = extract_forwarded_chat(message)
    if resolved is None:
        await message.answer(
            "❌ Не удалось определить чат по пересланному сообщению. "
            "Перешлите сообщение из группы/канала/чата.",
            reply_markup=kb.cancel_kb(target),
        )
        return
    await _add_chat(target, resolved.chat_id, resolved.title, resolved.kind, sources, receivers)
    await message.answer("Добавлено ✅")
    await render_list(message, state, target=target, sources=sources, receivers=receivers)


async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


def build_router() -> Router:
    """Собирает свежий `Router` источников на каждый вызов (см. докстринг модуля)."""
    router = Router(name="panel.sources")

    router.message.register(cmd_start, CommandStart())
    router.message.register(on_toggle_monitoring, F.text.in_({kb.BTN_PAUSE_ON, kb.BTN_PAUSE_OFF}))
    router.message.register(cmd_sources_menu, F.text == kb.BTN_SOURCES)

    router.callback_query.register(on_list_page, kb.ListPageCB.filter())
    router.callback_query.register(on_delete, kb.DeleteCB.filter())
    router.callback_query.register(on_pause_toggle, kb.PauseToggleCB.filter())
    router.callback_query.register(on_scan_now, kb.ScanCB.filter())
    router.callback_query.register(on_add_start, kb.AddStartCB.filter())
    router.callback_query.register(on_cancel_add, kb.CancelAddCB.filter())
    router.callback_query.register(
        on_add_method, kb.AddMethodCB.filter(), AddChatStates.choosing_method
    )
    router.callback_query.register(
        on_dialog_page, kb.DialogPageCB.filter(), AddChatStates.browsing_dialogs
    )
    router.callback_query.register(
        on_dialog_search_prompt, kb.DialogSearchCB.filter(), AddChatStates.browsing_dialogs
    )
    router.message.register(on_dialog_search_text, AddChatStates.waiting_search)
    router.callback_query.register(
        on_dialog_pick, kb.DialogPickCB.filter(), AddChatStates.browsing_dialogs
    )
    router.message.register(on_manual_text, AddChatStates.waiting_manual)
    router.message.register(on_forward, AddChatStates.waiting_forward)
    router.callback_query.register(on_noop, F.data == kb.NOOP)

    return router
