"""Фейковый Telethon-клиент и события — тестовый шов из interfaces.md.

Ничего не ходит в сеть и не открывает реальный аккаунт: `FakeTelegramClient`
запоминает зарегистрированные хендлеры (`add_event_handler`, как у настоящего
Telethon-клиента) и умеет "выстрелить" фейковым событием в сохранённый хендлер.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeSender:
    id: int
    bot: bool = False
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


@dataclass
class FakeEvent:
    """Дублирует поверхность Telethon NewMessage/MessageEdited.Event, которой пользуется listener."""

    chat_id: int
    id: int
    raw_text: str
    sender_id: int | None = None
    out: bool = False
    grouped_id: int | None = None
    sender: FakeSender | None = None

    async def get_sender(self):
        return self.sender


@dataclass
class FakeAlbumEvent:
    """Дублирует поверхность Telethon events.Album.Event: список сообщений одного поста."""

    chat_id: int
    messages: list[FakeEvent] = field(default_factory=list)
    sender_id: int | None = None
    out: bool = False
    sender: FakeSender | None = None

    async def get_sender(self):
        return self.sender


class FakeTelegramClient:
    """Минимум интерфейса TelegramClient, которым пользуется userbot: регистрация
    хендлеров и запись отправленных уведомлений. Тесты сами вызывают сохранённые
    хендлеры фейковыми событиями через `fire()`.
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list] = {}
        self.sent_messages: list[tuple[str, str]] = []

    def add_event_handler(self, callback, event_builder=None) -> None:
        key = type(event_builder) if event_builder is not None else None
        self._handlers.setdefault(key, []).append(callback)

    async def fire(self, event_builder_type: type, event) -> None:
        for callback in self._handlers.get(event_builder_type, []):
            await callback(event)

    async def send_message(self, chat, text) -> None:
        self.sent_messages.append((chat, text))
