"""Приём и обработка входящих/отредактированных сообщений (R22-R26, R32-R34, R76i).

Пайплайн (спека §4): отсечь себя/ботов/приёмник/чужой чат → прогнать текст (+
подписи, альбом целиком) через `matcher` → дедуп по id (уникальный индекс `hits`)
и по автору+тексту (`store`) → записать `hits` + по одной `delivery_queue` на
каждый активный приёмник → сдвинуть `sources.last_processed_msg_id`.

Реальный Telethon-клиент и его события — деталь `register_handlers`; сам пайплайн
(`process`) работает с нормализованным `IncomingMessage` и ничего не знает про
Telethon, поэтому тестируется без сети.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Awaitable, Callable

from telethon import events

import matcher
import store


@dataclass(frozen=True)
class IncomingMessage:
    """Нормализованный вход пайплайна — общий и для live-событий, и для догона."""

    chat_id: int
    message_id: int
    text: str
    is_own: bool
    sender_is_bot: bool
    author_id: int | None
    author_username: str | None
    author_name: str | None


@dataclass
class Deps:
    """Зависимости обработчиков: репозитории `store` + поставщик актуального `Ruleset`."""

    sources: store.SourcesRepo
    receivers: store.ReceiversRepo
    hits: store.HitsRepo
    delivery_queue: store.DeliveryQueueRepo
    settings: store.SettingsRepo
    ruleset_provider: Callable[[], matcher.Ruleset]


def text_hash(text: str) -> str:
    """Нормализованный хеш текста для дедупа по автору+тексту (R33)."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


async def process(
    msg: IncomingMessage,
    *,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    ruleset: matcher.Ruleset,
) -> store.Hit | None:
    """Пайплайн §4. Возвращает записанный `Hit` при новом срабатывании, иначе None."""
    if msg.is_own or msg.sender_is_bot:
        return None

    if await receivers.get(chat_id=msg.chat_id) is not None:
        return None

    source = await sources.get(chat_id=msg.chat_id)
    if source is None or source.paused:
        return None

    if source.kind == "private":
        monitor_all_dm = await settings.get("monitor_all_dm", "0")
        if monitor_all_dm != "1":
            return None

    result = ruleset.match(msg.text)
    if not result.matched:
        return None

    thash = text_hash(msg.text)
    dedup_window = int(await settings.get("dedup_window_days", "7"))
    dedup_enabled = (await settings.get("dedup_enabled", "1")) == "1"

    is_author_dup = await hits.is_duplicate_by_author_text(
        author_id=msg.author_id,
        text_hash=thash,
        window_days=dedup_window,
        dedup_enabled=dedup_enabled,
    )
    if is_author_dup:
        await _append_also_in(hits, author_id=msg.author_id, thash=thash, chat_id=msg.chat_id)
        return None

    try:
        hit = await hits.add(
            source_chat_id=msg.chat_id,
            message_id=msg.message_id,
            matched_keywords=result.hit_keywords,
            text_hash=thash,
            text_preview=msg.text,
            author_id=msg.author_id,
            author_username=msg.author_username,
            author_name=msg.author_name,
        )
    except store.DuplicateHitError:
        # Тот же message_id уже записан (повторная обработка правки, R25.1/R32).
        return None

    for receiver in await receivers.list():
        await delivery_queue.add(hit.id, receiver.chat_id)

    await sources.update(source.id, last_processed_msg_id=msg.message_id)

    return hit


async def _append_also_in(hits: store.HitsRepo, *, author_id, thash: str, chat_id: int) -> None:
    """Дописывает «также в: …» к самой свежей записи того же автора+текста (R33)."""
    existing = await hits.list()
    original = next(
        (h for h in existing if h.author_id == author_id and h.text_hash == thash),
        None,
    )
    if original is not None and chat_id not in original.also_in:
        await hits.update_also_in(original.id, [*original.also_in, chat_id])


def _full_name(sender) -> str | None:
    first = getattr(sender, "first_name", None) or ""
    last = getattr(sender, "last_name", None) or ""
    name = f"{first} {last}".strip()
    return name or None


async def build_incoming(source) -> IncomingMessage:
    """Строит `IncomingMessage` из Telethon-события/сообщения (или фейка с тем же интерфейсом)."""
    sender = await source.get_sender()
    return IncomingMessage(
        chat_id=source.chat_id,
        message_id=source.id,
        text=source.raw_text or "",
        is_own=bool(getattr(source, "out", False)),
        sender_is_bot=bool(getattr(sender, "bot", False)) if sender is not None else False,
        author_id=getattr(source, "sender_id", None),
        author_username=getattr(sender, "username", None) if sender is not None else None,
        author_name=_full_name(sender) if sender is not None else None,
    )


async def _from_album_event(event) -> IncomingMessage:
    """Строит один `IncomingMessage` на весь альбом (R24) — не по одному на фото."""
    sender = await event.get_sender()
    captions = [m.raw_text for m in event.messages if getattr(m, "raw_text", None)]
    first = min(event.messages, key=lambda m: m.id)
    return IncomingMessage(
        chat_id=event.chat_id,
        message_id=first.id,
        text="\n".join(captions),
        is_own=bool(getattr(event, "out", False)),
        sender_is_bot=bool(getattr(sender, "bot", False)) if sender is not None else False,
        author_id=getattr(event, "sender_id", None),
        author_username=getattr(sender, "username", None) if sender is not None else None,
        author_name=_full_name(sender) if sender is not None else None,
    )


def register_handlers(client, deps: Deps) -> None:
    """Регистрирует NewMessage/MessageEdited/Album-хендлеры на реальном/фейковом клиенте.

    Альбомы фильтруются из NewMessage/MessageEdited (`grouped_id is not None`) —
    их целиком обрабатывает отдельный `events.Album`-хендлер (R24).
    """

    async def on_new_message(event) -> None:
        if getattr(event, "grouped_id", None) is not None:
            return
        await _handle(await build_incoming(event), deps)

    async def on_edit(event) -> None:
        if getattr(event, "grouped_id", None) is not None:
            return
        await _handle(await build_incoming(event), deps)

    async def on_album(event) -> None:
        await _handle(await _from_album_event(event), deps)

    client.add_event_handler(on_new_message, events.NewMessage())
    client.add_event_handler(on_edit, events.MessageEdited())
    client.add_event_handler(on_album, events.Album())


async def _handle(msg: IncomingMessage, deps: Deps) -> None:
    await process(
        msg,
        sources=deps.sources,
        receivers=deps.receivers,
        hits=deps.hits,
        delivery_queue=deps.delivery_queue,
        settings=deps.settings,
        ruleset=deps.ruleset_provider(),
    )
