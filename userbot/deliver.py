"""Исполнение доставки одного срабатывания в один приёмник (R27-R31).

Форвард с фолбэком на копию текста при `ChatForwardsRestrictedError`, следом —
карточка источника отдельным сообщением. Ретраи, растущая задержка, глобальный
лимит и уведомление владельца при финальном неуспехе (R43, R61-R64) — забота
`worker`, не этого модуля: сюда приходит только «доставь один раз», остальные
исключения Telethon (`FloodWaitError`, `AuthKeyUnregisteredError` и т.п.)
специально не перехватываются здесь и всплывают к вызывающему.

`client` — Telethon `TelegramClient` (или фейк с тем же интерфейсом в тестах,
см. `tests/conftest.py`): `forward_messages`, `get_messages`, `send_message`,
`get_entity`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telethon.errors import ChatForwardsRestrictedError

from store import Hit, Receiver, Source

DEFAULT_CARD_TEMPLATE = "🔑 {keywords}\n👤 {author}\n💬 {chat}\n🕐 {time}"


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    used_copy_fallback: bool = False


async def deliver(
    client,
    hit: Hit,
    source: Source,
    receiver: Receiver,
    *,
    card_template: str = DEFAULT_CARD_TEMPLATE,
    tz: str = "Europe/Moscow",
) -> DeliveryResult:
    """Форвард (или копия текста, если чат запрещает форвард) + карточка следом."""
    used_copy_fallback = False
    try:
        await client.forward_messages(receiver.chat_id, hit.message_id, source.chat_id)
    except ChatForwardsRestrictedError:
        used_copy_fallback = True
        message = await client.get_messages(source.chat_id, hit.message_id)
        text = getattr(message, "text", None) or getattr(message, "message", None) or hit.text_preview
        await client.send_message(receiver.chat_id, text)

    link = await build_original_link(client, source.chat_id, hit.message_id)
    card = build_card(card_template, hit=hit, source=source, link=link, tz=tz)
    await client.send_message(receiver.chat_id, card)

    return DeliveryResult(ok=True, used_copy_fallback=used_copy_fallback)


def build_card(template: str, *, hit: Hit, source: Source, link: str, tz: str = "Europe/Moscow") -> str:
    """Рендерит карточку источника по шаблону (R28, R31)."""
    return template.format(
        keywords=", ".join(hit.matched_keywords),
        author=_format_author(hit),
        chat=_format_chat(source, link),
        time=_format_time(hit.created_at, tz),
    )


def _format_author(hit: Hit) -> str:
    name = hit.author_name or "Без имени"
    author_id = hit.author_id
    if hit.author_username:
        return f"{name} · @{hit.author_username} · id {author_id}"
    if author_id is None:
        return f"{name} · id неизвестен"
    return f"{name} · tg://user?id={author_id} · id {author_id}"


def _format_chat(source: Source, link: str) -> str:
    return f"Чат: «{source.title}» · {link}"


def _format_time(created_at: str, tz: str) -> str:
    naive = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    aware_utc = naive.replace(tzinfo=timezone.utc)
    local = aware_utc.astimezone(ZoneInfo(tz))
    return local.strftime("%d.%m.%Y %H:%M")


async def build_original_link(client, chat_id: int, message_id: int) -> str:
    """`t.me/<chat>/<id>` для публичных чатов, `t.me/c/<internal_id>/<id>` для приватных
    (R30) — единственное место, где строится эта ссылка; `panel.stats` (история,
    R52) переиспользует её напрямую, а не держит вторую копию логики."""
    entity = await client.get_entity(chat_id)
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal_id = _to_internal_id(chat_id)
    return f"https://t.me/c/{internal_id}/{message_id}"


def _to_internal_id(chat_id: int) -> int:
    text = str(chat_id)
    if text.startswith("-100"):
        return int(text[4:])
    return abs(chat_id)
