"""aiogram-панель: меню, FSM мастеров ввода, чтение/запись через `store`.

`build_router(owner_id)` — единственная публичная точка сборки тикета 04:
оборачивает источники+приёмники в `OwnerAccessMiddleware` (R59) и отдаёт один
`Router`, готовый для `Dispatcher.include_router()`. Тикет 07 подключает его и
рядом — роутеры тикета 05 (`panel.keywords`/`panel.stats`/`panel.export`, ещё
не существуют на момент тикета 04).

Хендлеры ожидают через DI (аргументы по имени, как в aiogram-инъекции —
передаются `Dispatcher.start_polling(bot, **kwargs)` / `feed_update(bot,
update, **kwargs)`): `sources: store.SourcesRepo`, `receivers:
store.ReceiversRepo`, `keywords: store.KeywordsRepo`, `hits: store.HitsRepo`,
`delivery_queue: store.DeliveryQueueRepo`, `settings: store.SettingsRepo`,
`tg_client` (Telethon `TelegramClient` или дублирующий интерфейс фейк — тот
же клиент, что строит `userbot.build_client()`, нужен панели только для
`userbot.list_dialogs`/`userbot.catchup.scan_source`/`client.get_me()`).
"""
from __future__ import annotations

from aiogram import Router

from .auth import OwnerAccessMiddleware
from . import keyboards
from . import receivers as receivers_router_module
from . import sources as sources_router_module

__all__ = ["build_router", "keyboards"]


def build_router(owner_id: int) -> Router:
    """Собирает роутер панели тикета 04 (источники/приёмники) с проверкой доступа.

    Middleware вешается один раз здесь — под-роутеры (`sources`, `receivers`)
    сами доступ не проверяют, полагаются на обёртку. Роутеры следующих
    тикетов (05) либо подключаются сюда же (тогда получают ту же защиту
    бесплатно), либо оборачиваются `OwnerAccessMiddleware` отдельно, если
    подключаются напрямую к `Dispatcher`.
    """
    root = Router(name="panel")
    middleware = OwnerAccessMiddleware(owner_id)
    root.message.outer_middleware(middleware)
    root.callback_query.outer_middleware(middleware)
    root.include_router(sources_router_module.build_router())
    root.include_router(receivers_router_module.build_router())
    root.startup.register(receivers_router_module.on_startup)
    return root
