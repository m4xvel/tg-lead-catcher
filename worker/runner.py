"""Фоновый цикл доставки: читает `delivery_queue` со `status=pending`, не превышая
глобальный лимит, ретраит с растущей задержкой, реагирует на `FloodWaitError` и на
смерть сессии (R43, R61-R64). Живёт внутри процесса `userbot` (единственный, кто
может форвардить) — вызывающий передаёт уже готовый Telethon-клиент (или фейк).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from telethon.errors import ChatForwardsRestrictedError, FloodWaitError
from telethon.errors.rpcerrorlist import AuthKeyUnregisteredError, SessionRevokedError

import store
from userbot.client import notify_owner
from userbot.deliver import deliver

from .bot_notify import notify_owner_via_bot_api
from .ratelimit import RateLimiter

logger = logging.getLogger("worker")

MAX_ATTEMPTS = 3
# растущая задержка между попытками: 30с, 120с, 480с (R61)
BACKOFF_BASE_SECONDS = 30
BACKOFF_FACTOR = 4

SESSION_DEAD_ERRORS = (AuthKeyUnregisteredError, SessionRevokedError)
SESSION_DEAD_TEXT = "⚠️ Сессия отвалилась, запусти вход заново"


def backoff_seconds(attempts: int) -> int:
    return BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR ** max(attempts - 1, 0))


def _now_str(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Cycle:
    """Итог одного прохода очереди — сигнал `run()`, продолжать ли цикл."""

    session_dead: bool = False


async def process_pending(
    conn,
    client,
    *,
    limiter: RateLimiter,
    now: datetime | None = None,
    sleep=asyncio.sleep,
    http_post=None,
) -> Cycle:
    """Один проход: обрабатывает все дозревшие записи `delivery_queue` со `status=pending`."""
    now = now or datetime.now(timezone.utc)
    queue = store.DeliveryQueueRepo(conn)
    hits_repo = store.HitsRepo(conn)
    sources_repo = store.SourcesRepo(conn)
    receivers_repo = store.ReceiversRepo(conn)
    settings_repo = store.SettingsRepo(conn)

    card_template = await settings_repo.get(
        "card_template", "🔑 {keywords}\n👤 {author}\n💬 {chat}\n🕐 {time}"
    )
    tz = await settings_repo.get("tz", "Europe/Moscow")

    for task in await queue.list(status="pending"):
        if task.next_attempt_at and task.next_attempt_at > _now_str(now):
            continue  # ждёт своей растущей задержки

        hit = await hits_repo.get(task.hit_id)
        receiver = await receivers_repo.get(chat_id=task.receiver_chat_id)
        source = await sources_repo.get(chat_id=hit.source_chat_id) if hit else None
        if hit is None or receiver is None or source is None:
            await queue.update(task.id, status="failed", error="hit/receiver/источник не найден")
            continue

        await limiter.wait_for_slot(sleep=sleep)

        try:
            await deliver(client, hit, source, receiver, card_template=card_template, tz=tz)
        except FloodWaitError as exc:
            # R63: попытка не тратится, ждём ровно указанное время, пишем в лог
            logger.warning(
                "FloodWaitError на задаче #%s: спим %s секунд", task.id, exc.seconds
            )
            await sleep(exc.seconds)
            continue
        except SESSION_DEAD_ERRORS as exc:
            # Состояние фиксируем в БД ДО попытки уведомить — статус должен быть
            # виден (в т.ч. будущей панели, тикет 05) независимо от того, дойдёт
            # ли уведомление вообще.
            await settings_repo.set("monitoring_paused", "1")
            await settings_repo.set("monitoring_paused_reason", "session_dead")
            logger.error("Сессия userbot умерла (%s), мониторинг остановлен", type(exc).__name__)
            # Сама умершая MTProto-сессия не может доставить уведомление о своей
            # смерти — единственный переживающий это канал — отдельный бот на
            # BOT_TOKEN (Bot API напрямую, не userbot.notify_owner). Оборачиваем
            # в try/except: неудача уведомления не должна ронять process_pending —
            # мониторинг уже остановлен флагом выше.
            try:
                bot_token = os.environ.get("BOT_TOKEN", "")
                owner_id = int(os.environ.get("OWNER_ID", "0") or "0")
                sent = await notify_owner_via_bot_api(
                    bot_token, owner_id, SESSION_DEAD_TEXT, http_post=http_post
                )
                if not sent:
                    logger.error("Не удалось уведомить владельца о смерти сессии через Bot API")
            except Exception:
                logger.exception("Ошибка при попытке уведомить владельца о смерти сессии")
            return Cycle(session_dead=True)
        except ChatForwardsRestrictedError:
            # deliver() сам обрабатывает эту ошибку фолбэком на копию — сюда она
            # долетать не должна; страхуемся, чтобы не тратить попытку впустую.
            continue
        except Exception as exc:  # noqa: BLE001 — любая другая ошибка доставки: ретрай (R61)
            attempts = task.attempts + 1
            if attempts >= MAX_ATTEMPTS:
                await queue.update(task.id, status="failed", attempts=attempts, error=str(exc))
                # Сессия жива (это не смерть сессии — обычная ошибка доставки), но
                # сама попытка уведомить всё равно не должна ронять process_pending.
                try:
                    await notify_owner(
                        client,
                        f"⚠️ Не удалось доставить срабатывание #{task.hit_id} "
                        f"в приёмник {task.receiver_chat_id} после {attempts} попыток: {exc}",
                    )
                except Exception:
                    logger.exception(
                        "Не удалось уведомить владельца о провале доставки задачи #%s", task.id
                    )
            else:
                next_attempt = now + timedelta(seconds=backoff_seconds(attempts))
                await queue.update(
                    task.id,
                    attempts=attempts,
                    next_attempt_at=_now_str(next_attempt),
                    error=str(exc),
                )
            continue

        await queue.update(task.id, status="sent")

    return Cycle()


async def run(
    conn,
    client,
    *,
    poll_interval: float = 5.0,
    sleep=asyncio.sleep,
    http_post=None,
) -> None:
    """Фоновый цикл (R61): забирает pending из delivery_queue, пока не умрёт сессия."""
    settings_repo = store.SettingsRepo(conn)
    while True:
        rate_limit = int(await settings_repo.get("rate_limit_per_min", "20"))
        limiter = RateLimiter(rate_limit)
        cycle = await process_pending(conn, client, limiter=limiter, sleep=sleep, http_post=http_post)
        if cycle.session_dead:
            return
        await sleep(poll_interval)
