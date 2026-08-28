"""Приёмники (R03, R42): переиспользует каркас списка/добавления `panel.sources`
(те же три способа добавления, R38-R40) и добавляет «💾 Избранное» — приёмник
по умолчанию при первом запуске панели (через `router.startup`).

`build_router()` — фабрика (см. докстринг `panel.sources` — почему не модульный
синглтон): свежий `Router` на каждый вызов.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import store

from . import keyboards as kb
from . import sources as sources_flow


async def ensure_default_receiver(receivers: store.ReceiversRepo, tg_client) -> None:
    """«Избранное» становится приёмником по умолчанию, если список ещё пуст (R42).

    Идемпотентно: при непустом списке ничего не делает, поэтому безопасно
    вызывать и на каждом старте панели (`router.startup`), и по кнопке.
    """
    if await receivers.list():
        return
    me = await tg_client.get_me()
    try:
        await receivers.add(me.id, kb.BTN_FAVORITE)
    except store.DuplicateChatError:
        pass


async def on_startup(receivers: store.ReceiversRepo, tg_client) -> None:
    """Регистрируется как `router.startup` — засевает «Избранное» при первом запуске."""
    await ensure_default_receiver(receivers, tg_client)


async def cmd_receivers_menu(
    message: Message,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
) -> None:
    await sources_flow.render_list(
        message, state, target=kb.TARGET_RECEIVER, sources=sources, receivers=receivers
    )


async def add_favorite_receiver(receivers: store.ReceiversRepo, tg_client) -> bool:
    """Явное нажатие «💾 Избранное» (R42): добавляет «Избранное» в приёмники,
    если его там ещё нет — независимо от того, пуст список или нет.

    В отличие от `ensure_default_receiver` (автозасев при первом запуске,
    guard «только если список пуст»), это отдельная явная операция и не должна
    молчать, когда в списке уже есть другие приёмники — иначе кнопка выглядит
    нажатой, а на деле ничего не происходит.

    Возвращает True, если «Избранное» реально добавлено этим вызовом, и False,
    если оно уже было в приёмниках (ничего не изменилось).
    """
    me = await tg_client.get_me()
    if await receivers.get(chat_id=me.id) is not None:
        return False
    try:
        await receivers.add(me.id, kb.BTN_FAVORITE)
    except store.DuplicateChatError:
        return False
    return True


async def on_add_favorite(
    callback: CallbackQuery,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    tg_client,
) -> None:
    added = await add_favorite_receiver(receivers, tg_client)
    if added:
        await callback.answer("Избранное добавлено ✅")
    else:
        await callback.answer("Избранное уже в приёмниках")
    await sources_flow.render_list(
        callback, state, target=kb.TARGET_RECEIVER, sources=sources, receivers=receivers
    )


def build_router() -> Router:
    router = Router(name="panel.receivers")
    router.message.register(cmd_receivers_menu, F.text == kb.BTN_RECEIVERS)
    router.callback_query.register(on_add_favorite, kb.FavoriteAddCB.filter())
    return router
