"""aiogram-панель: меню, FSM мастеров ввода, чтение/запись через `store`.

`build_router(owner_id)` — единственная публичная точка сборки панели (тикеты
04+05): оборачивает источники/приёмники/ключи/статистику/экспорт в общий
`OwnerAccessMiddleware` (R59) и отдаёт один `Router`, готовый для
`Dispatcher.include_router()` (тикет 07 больше ничего не оборачивает сам).

Хендлеры ожидают через DI (аргументы по имени, как в aiogram-инъекции —
передаются `Dispatcher.start_polling(bot, **kwargs)` / `feed_update(bot,
update, **kwargs)`): `sources: store.SourcesRepo`, `receivers:
store.ReceiversRepo`, `keywords: store.KeywordsRepo`, `hits: store.HitsRepo`,
`delivery_queue: store.DeliveryQueueRepo`, `settings: store.SettingsRepo`,
`tg_client` (Telethon `TelegramClient` или дублирующий интерфейс фейк — тот
же клиент, что строит `userbot.build_client()`, нужен панели только для
`userbot.list_dialogs`/`userbot.catchup.scan_source`/`client.get_me()`).

Порядок `include_router` ниже критичен для R53: `panel.stats` и
`panel.sources` оба регистрируют `on_toggle_monitoring` на одну и ту же кнопку
паузы — aiogram отдаёт апдейт первому совпавшему хендлеру. `stats` должен идти
раньше `sources`, иначе диалог о догоне (R53) никогда не покажется, сработает
только мгновенный тумблер из `sources` (R48). См. «Из тикета 05» в
interfaces.md.
"""
from __future__ import annotations

from aiogram import Router

from .auth import OwnerAccessMiddleware
from . import export as export_router_module
from . import keyboards
from . import keywords as keywords_router_module
from . import receivers as receivers_router_module
from . import sources as sources_router_module
from . import stats as stats_router_module

__all__ = ["build_router", "keyboards"]


def build_router(owner_id: int) -> Router:
    """Собирает единый роутер панели (источники/приёмники/ключи/статистика/экспорт)
    с проверкой доступа.

    Middleware вешается один раз здесь — ни один под-роутер сам доступ не
    проверяет, все полагаются на эту обёртку.
    """
    root = Router(name="panel")
    middleware = OwnerAccessMiddleware(owner_id)
    root.message.outer_middleware(middleware)
    root.callback_query.outer_middleware(middleware)
    # R53: stats — ПЕРЕД sources (см. докстринг модуля выше и «Из тикета 05»
    # в interfaces.md) — иначе диалог о догоне при снятии паузы не сработает.
    root.include_router(stats_router_module.build_router())
    root.include_router(keywords_router_module.build_router())
    root.include_router(export_router_module.build_router())
    root.include_router(sources_router_module.build_router())
    root.include_router(receivers_router_module.build_router())
    root.startup.register(receivers_router_module.on_startup)
    return root
