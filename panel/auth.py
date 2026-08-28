"""Проверка доступа владельца (R59): сверка `from_user.id` с `OWNER_ID`.

Постороннему — «Доступ запрещён», попытка логируется, хендлер не вызывается.
Лог — независимый от `hits` (спека §8): обычный `logging`, не таблица в БД.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger("panel.auth")

DENIED_TEXT = "🚫 Доступ запрещён."


class OwnerAccessMiddleware(BaseMiddleware):
    """Внешний (outer) middleware для message/callback_query.

    Вешается на роутер(ы) панели — обычно один раз на верхнем уровне
    диспетчера (см. `panel.build_router`), так что защищает все под-роутеры
    сразу, включая те, что добавят следующие тикеты (05).
    """

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id != self.owner_id:
            logger.warning(
                "Доступ запрещён: id=%s username=%s",
                getattr(user, "id", None),
                getattr(user, "username", None),
            )
            answer = getattr(event, "answer", None)
            if answer is not None:
                await answer(DENIED_TEXT)
            return None
        return await handler(event, data)
