"""Резервный канал уведомлений владельцу через Bot API (R64) — только для смерти
userbot-сессии (`AuthKeyUnregisteredError`/`SessionRevokedError`).

Обычные уведомления (R43/R76i) по-прежнему идут через `userbot.notify_owner` —
та же MTProto-сессия, что и остальной userbot, это работает, пока сессия жива.
Но при смерти самой сессии слать через неё же уведомление бессмысленно: это тот
клиент, который только что умер. Единственный канал, переживающий смерть
userbot-сессии — отдельный бот на `BOT_TOKEN` (обычный HTTP POST в Bot API, без
aiogram — он не в зоне `worker/`).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("worker")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def _default_http_post(url: str, payload: dict) -> int:
    """Реальный HTTP POST через aiohttp (уже тянется как зависимость aiogram).
    Возвращает HTTP-статус ответа."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return resp.status


async def notify_owner_via_bot_api(
    bot_token: str,
    owner_id: int,
    text: str,
    *,
    http_post=None,
) -> bool:
    """POST на Bot API `sendMessage` напрямую (без aiogram).

    `http_post` — асинхронная функция `(url, payload) -> int` (HTTP-статус),
    для инъекции фейка в тестах (по умолчанию — реальный запрос через aiohttp).

    Последний рубеж оповещения: никогда не бросает исключение наружу — любая
    сетевая ошибка логируется и превращается в `False`, чтобы попытка уведомить
    не могла сама уронить воркер.
    """
    post = http_post or _default_http_post
    url = TELEGRAM_API_URL.format(token=bot_token)
    payload = {"chat_id": owner_id, "text": text}
    try:
        status = await post(url, payload)
    except Exception:
        logger.exception("Не удалось отправить уведомление владельцу через Bot API")
        return False

    ok = 200 <= status < 300
    if not ok:
        logger.error("Bot API вернул статус %s при уведомлении владельца", status)
    return ok
