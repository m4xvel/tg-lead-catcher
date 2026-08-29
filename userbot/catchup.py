"""Догон истории: без предохранителя при старте (R35/R54), с предохранителем по
кнопке/снятию паузы источника (R37/R78i). Источник, ставший недоступным, —
паузится и порождает одно уведомление владельцу (R76i), тем же каналом, что и
у тикета 03 (`userbot.client.notify_owner`).
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Awaitable, Callable

from telethon import errors as tg_errors

import matcher
import store

from . import listener
from .client import notify_owner

DEFAULT_PAUSE_SECONDS = 1.0
HARD_MSG_CAP = 500  # предохранитель ручного скана — не больше 500 сообщений за вызов (R37)

# Ошибки Telethon, которыми проявляется недоступность чата (кикнули, чат удалён,
# аккаунт деактивирован) — источник, упавший на любой из них, паузится (R76i).
INACCESSIBLE_SOURCE_ERRORS = (
    tg_errors.ChannelPrivateError,
    tg_errors.ChannelInvalidError,
    tg_errors.ChatAdminRequiredError,
    tg_errors.ChatInvalidError,
    tg_errors.UserDeactivatedError,
    tg_errors.UserDeactivatedBanError,
    tg_errors.UserNotParticipantError,
    tg_errors.ChatWriteForbiddenError,
)


async def catch_up(
    client,
    chat_id: int,
    since_id: int | None,
    *,
    limit_days: int | None = None,
    limit_msgs: int | None = None,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
    process: Callable[[object], Awaitable[None]] | None = None,
) -> int | None:
    """Дочитывает сообщения `chat_id` новее `since_id` (по возрастанию id).

    ``limit_days``/``limit_msgs`` = None → без предохранителя (старт программы,
    R35). Заданные значения — предохранитель ручного скана (R37): не глубже
    ``limit_days``, не больше ``limit_msgs``, с паузой между запросами Telethon.

    ``process`` — асинхронный колбэк на каждое найденное сообщение (обычно —
    обёртка над `listener.process`). Возвращает наибольший увиденный id
    сообщения (для сдвига `sources.last_processed_msg_id`) или None, если
    новых сообщений не было.
    """
    min_date = None
    if limit_days is not None:
        min_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=limit_days
        )

    kwargs: dict = {"reverse": True}
    if since_id is not None:
        kwargs["min_id"] = since_id

    seen = 0
    max_id: int | None = None
    async for message in client.iter_messages(chat_id, **kwargs):
        if min_date is not None and message.date is not None and message.date < min_date:
            continue
        if limit_msgs is not None and seen >= limit_msgs:
            break
        seen += 1
        max_id = message.id if max_id is None else max(max_id, message.id)
        if process is not None:
            await process(message)
        if pause_seconds:
            await asyncio.sleep(pause_seconds)

    return max_id


async def scan_source(
    client,
    source: store.Source,
    *,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    ruleset_provider: Callable[[], matcher.Ruleset],
    limit_days: int | None = None,
    limit_msgs: int | None = None,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
) -> int | None:
    """Скан одного источника с предохранителем (R37) и сдвигом `last_processed_msg_id`.

    Используется и кнопкой «Просканировать N дней» (``limit_days`` явно задан
    вызывающим, до 7), и снятием индивидуальной паузы источника (R78i, без
    вопроса о догоне — вызывающий просто зовёт это с настройками по умолчанию).
    Без явного ``limit_days``/``limit_msgs`` берёт `settings.scan_days`/`scan_msgs`,
    но `limit_msgs` всегда зажат сверху `HARD_MSG_CAP` (500), даже если в
    настройках больше.
    """
    if limit_days is None:
        limit_days = int(await settings.get("scan_days", "3"))
    if limit_msgs is None:
        limit_msgs = int(await settings.get("scan_msgs", "500"))
    limit_msgs = min(limit_msgs, HARD_MSG_CAP)

    max_id = await _run_catch_up(
        client,
        source,
        sources=sources,
        receivers=receivers,
        hits=hits,
        delivery_queue=delivery_queue,
        settings=settings,
        ruleset_provider=ruleset_provider,
        limit_days=limit_days,
        limit_msgs=limit_msgs,
        pause_seconds=pause_seconds,
    )
    return max_id


async def run_startup_catchup(
    client,
    *,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    ruleset_provider: Callable[[], matcher.Ruleset],
    notify: Callable[[str], Awaitable[None]] | None = None,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
) -> dict[int, int | None]:
    """При старте — догон без предохранителя по каждому активному источнику (R35/R54).

    Источник, упавший на недоступности (кикнули, чат удалён), помечается
    `paused=True` и порождает ровно одно уведомление владельцу (R76i) — на
    следующем запуске он уже не в списке активных, повторных уведомлений нет.
    """
    if notify is None:
        async def notify(text: str) -> None:
            await notify_owner(client, text)

    results: dict[int, int | None] = {}
    for source in await sources.list():
        if source.paused:
            continue
        try:
            results[source.chat_id] = await _run_catch_up(
                client,
                source,
                sources=sources,
                receivers=receivers,
                hits=hits,
                delivery_queue=delivery_queue,
                settings=settings,
                ruleset_provider=ruleset_provider,
                limit_days=None,
                limit_msgs=None,
                pause_seconds=pause_seconds,
            )
        except INACCESSIBLE_SOURCE_ERRORS as exc:
            await sources.update(source.id, paused=True)
            await notify(
                f"Источник «{source.title}» стал недоступен (кикнули или чат "
                f"удалён) и поставлен на паузу: {exc}"
            )
            results[source.chat_id] = None
    return results


async def _run_catch_up(
    client,
    source: store.Source,
    *,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    ruleset_provider: Callable[[], matcher.Ruleset],
    limit_days: int | None,
    limit_msgs: int | None,
    pause_seconds: float,
) -> int | None:
    async def _process(message) -> None:
        msg = await listener.build_incoming(message)
        await listener.process(
            msg,
            sources=sources,
            receivers=receivers,
            hits=hits,
            delivery_queue=delivery_queue,
            settings=settings,
            ruleset=ruleset_provider(),
        )

    max_id = await catch_up(
        client,
        source.chat_id,
        source.last_processed_msg_id,
        limit_days=limit_days,
        limit_msgs=limit_msgs,
        pause_seconds=pause_seconds,
        process=_process,
    )
    if max_id is not None:
        # Сдвигаем указатель до конца скана независимо от того, сработал ли
        # матчинг на каждом сообщении — иначе тихий чат заставлял бы догон
        # каждый раз перечитывать всю историю заново (`process()` сам сдвигает
        # `last_processed_msg_id` только на новое срабатывание, см. listener.py).
        await sources.update(source.id, last_processed_msg_id=max_id)
    return max_id
