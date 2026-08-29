"""Статус, статистика, топ мусорных ключей, история и диалог догона при снятии
глобальной паузы (R49-R53).

`on_toggle_monitoring` здесь — полноценная замена одноимённого хендлера
`panel.sources` (который реализует только R48, без вопроса о догоне из R53).
`panel/sources.py` — вне зоны этого тикета и не тронут; чтобы R53 реально
сработало, роутер этого модуля должен быть подключён РАНЬШЕ `panel.build_router()`
в общей сборке (тикет 07) — иначе `on_toggle_monitoring` из `panel.sources`
перехватит нажатие первым. См. CONCERNS в отчёте тикета.

`build_router()` — фабрика (см. докстринг `panel.sources`): свежий `Router` на
каждый вызов.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import store
from userbot import catchup as userbot_catchup
from userbot import deliver as userbot_deliver
from worker.runner import SESSION_DEAD_TEXT

from . import keyboards as kb
from .keywords import build_ruleset

logger = logging.getLogger("panel.stats")

_DT_FORMAT = "%Y-%m-%d %H:%M:%S"
HISTORY_LIMIT = 50
TOP_KEYWORDS_LIMIT = 10
TOP_KEYWORDS_WINDOW_DAYS = 30
_RESUME_THRESHOLD_SECONDS = 60  # R53: диалог только после ≥1 минуты простоя


class ResumeStates(StatesGroup):
    confirming = State()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_dt(dt: datetime) -> str:
    return dt.strftime(_DT_FORMAT)


def _parse_dt(text: str) -> datetime:
    return datetime.strptime(text, _DT_FORMAT).replace(tzinfo=timezone.utc)


def format_pause_duration(seconds: int) -> str:
    """«3 ч 20 мин» — точный формат из брифа."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


async def _pause_duration_seconds(settings: store.SettingsRepo) -> int | None:
    """Реальная длительность текущей паузы (для вопроса о догоне R53) — `None`,
    если `monitoring_paused_at` не установлен."""
    paused_at_raw = await settings.get("monitoring_paused_at", None)
    if not paused_at_raw:
        return None
    return int((_now() - _parse_dt(paused_at_raw)).total_seconds())


@dataclass
class PeriodStats:
    total: int = 0
    by_keyword: Counter = field(default_factory=Counter)
    by_chat: Counter = field(default_factory=Counter)


def compute_period_stats(hits: list[store.Hit], days: int, *, now: datetime | None = None) -> PeriodStats:
    """Сработки за последние `days` суток (R50) — фильтрация по `hits.created_at`."""
    cutoff = (now or _now()) - timedelta(days=days)
    stats = PeriodStats()
    for hit in hits:
        created = _parse_dt(hit.created_at)
        if created < cutoff:
            continue
        stats.total += 1
        for keyword in hit.matched_keywords:
            stats.by_keyword[keyword] += 1
        stats.by_chat[hit.source_chat_id] += 1
    return stats


def top_keywords(stats: PeriodStats, limit: int = TOP_KEYWORDS_LIMIT) -> list[tuple[str, int]]:
    """«Топ мусорных ключей» (R51) — те же данные статистики, по убыванию количества."""
    return stats.by_keyword.most_common(limit)


def format_status(
    *, monitoring_paused: bool, sources_count: int, keywords_count: int,
    pending_count: int, failed_count: int, last_hit_at: str | None,
) -> str:
    lines = [
        "📈 Статус",
        "▶️ Мониторинг работает" if not monitoring_paused else "⏸ Мониторинг остановлен",
        f"Источников: {sources_count}",
        f"Ключей: {keywords_count}",
        f"В очереди доставки: ожидают {pending_count}, не доставлено {failed_count}",
        f"Последнее срабатывание: {last_hit_at}" if last_hit_at else "Последнее срабатывание: ещё не было",
    ]
    return "\n".join(lines)


async def format_stats(stats: PeriodStats, days: int, sources: store.SourcesRepo) -> str:
    """По чатам — человекочитаемый `title` (как везде в панели, см. `list_page_kb`
    и `userbot.deliver._format_chat`), не голый `chat_id`. Источник мог быть с тех
    пор удалён — тогда фолбэком показываем сам chat_id, а не падаем."""
    lines = [f"📊 Статистика за {days} дн.: сработок — {stats.total}"]
    if stats.by_keyword:
        lines.append("По ключам:")
        for keyword, count in stats.by_keyword.most_common():
            lines.append(f"  {keyword}: {count}")
    if stats.by_chat:
        lines.append("По чатам:")
        for chat_id, count in stats.by_chat.most_common():
            source = await sources.get(chat_id=chat_id)
            label = source.title if source is not None else str(chat_id)
            lines.append(f"  {label}: {count}")
    return "\n".join(lines)


def format_top_keywords(pairs: list[tuple[str, int]]) -> str:
    if not pairs:
        return "📊 Топ мусорных ключей: пока пусто."
    lines = ["📊 Топ мусорных ключей:"]
    for keyword, count in pairs:
        lines.append(f"  {keyword}: {count}")
    return "\n".join(lines)


class _EntityCachingClient:
    """Прокси вокруг `tg_client`, кеширующий `get_entity` по `chat_id` — живёт
    только на время одного вызова `format_history` (обычный `dict`, не через
    `store`, не персистентный). До 50 записей `hits` в истории часто относятся
    к одному и тому же чату — без кеша `build_original_link` резолвил бы его
    сетью заново на каждую запись."""

    def __init__(self, client, cache: dict):
        self._client = client
        self._cache = cache

    async def get_entity(self, chat_id):
        if chat_id not in self._cache:
            self._cache[chat_id] = await self._client.get_entity(chat_id)
        return self._cache[chat_id]

    def __getattr__(self, name):
        return getattr(self._client, name)


async def format_history(hits: list[store.Hit], tg_client) -> str:
    """Ссылка на оригинал строится той же функцией, что и карточка доставки
    (`userbot.deliver.build_original_link`, R30) — публичная форма для публичных
    чатов, приватная для остальных; вторая независимая копия этой логики не
    заводится. Сетевой `get_entity` внутри неё кешируется по `chat_id` на время
    вызова этой функции (`_EntityCachingClient`) — записи истории часто
    повторяют один и тот же chat_id, резолвить его сетью каждый раз незачем.

    Правка тикета P3: `get_entity`/`build_original_link` может упасть для
    ОДНОГО chat_id (чат стал недоступен, удалён и т.п.) — это не должно ронять
    весь экран истории из-за одной проблемной записи; такая строка просто
    показывается без ссылки, остальные рендерятся как обычно."""
    if not hits:
        return "🕐 История: пока пусто."
    lines = ["🕐 История (последние {}):".format(len(hits))]
    entity_cache: dict[int, object] = {}
    cached_client = _EntityCachingClient(tg_client, entity_cache)
    for hit in hits[:HISTORY_LIMIT]:
        try:
            link = await userbot_deliver.build_original_link(cached_client, hit.source_chat_id, hit.message_id)
        except Exception:
            logger.warning(
                "Не удалось построить ссылку для chat_id=%s message_id=%s",
                hit.source_chat_id, hit.message_id, exc_info=True,
            )
            link = "(ссылка недоступна)"
        keywords = ", ".join(hit.matched_keywords)
        lines.append(f"{hit.created_at} — {keywords} — {link}")
    return "\n".join(lines)


# --- хендлеры --------------------------------------------------------------


async def on_status(
    message: Message,
    sources: store.SourcesRepo,
    keywords: store.KeywordsRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
) -> None:
    paused = (await settings.get("monitoring_paused", "0")) == "1"
    all_sources = await sources.list()
    all_keywords = await keywords.list(is_stop=False)
    pending = await delivery_queue.list(status="pending")
    failed = await delivery_queue.list(status="failed")
    recent = await hits.list(limit=1)
    last_hit_at = recent[0].created_at if recent else None
    await message.answer(
        format_status(
            monitoring_paused=paused,
            sources_count=len(all_sources),
            keywords_count=len(all_keywords),
            pending_count=len(pending),
            failed_count=len(failed),
            last_hit_at=last_hit_at,
        )
    )


async def _render_stats(
    event: Message | CallbackQuery, hits: store.HitsRepo, sources: store.SourcesRepo, days: int
) -> None:
    all_hits = await hits.list()
    stats = compute_period_stats(all_hits, days)
    text = await format_stats(stats, days, sources)
    markup = kb.stats_period_kb(days)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


async def on_stats_menu(message: Message, hits: store.HitsRepo, sources: store.SourcesRepo) -> None:
    await _render_stats(message, hits, sources, days=7)


async def on_stats_period(
    callback: CallbackQuery, callback_data: kb.StatsPeriodCB, hits: store.HitsRepo, sources: store.SourcesRepo
) -> None:
    await _render_stats(callback, hits, sources, days=callback_data.days)


async def on_top_keywords(message: Message, hits: store.HitsRepo) -> None:
    all_hits = await hits.list()
    stats = compute_period_stats(all_hits, TOP_KEYWORDS_WINDOW_DAYS)
    await message.answer(format_top_keywords(top_keywords(stats)))


async def on_history(message: Message, hits: store.HitsRepo, tg_client) -> None:
    recent = await hits.list(limit=HISTORY_LIMIT)
    await message.answer(await format_history(recent, tg_client))


async def on_toggle_monitoring(
    message: Message,
    state: FSMContext,
    settings: store.SettingsRepo,
) -> None:
    """Глобальный тумблер (R48) + вопрос о догоне при снятии паузы (R53)."""
    was_paused = (await settings.get("monitoring_paused", "0")) == "1"
    reason = await settings.get("monitoring_paused_reason", "")
    if was_paused and reason == "session_dead":
        await message.answer(
            f"{SESSION_DEAD_TEXT} (`python configure.py`), потом включай мониторинг.",
            reply_markup=kb.main_menu_kb(monitoring_paused=True),
        )
        return

    if not was_paused:
        await settings.set("monitoring_paused", "1")
        await settings.set("monitoring_paused_at", _format_dt(_now()))
        await message.answer(
            "⏸ Мониторинг остановлен.", reply_markup=kb.main_menu_kb(monitoring_paused=True)
        )
        return

    duration = await _pause_duration_seconds(settings)

    if duration is None or duration < _RESUME_THRESHOLD_SECONDS:
        await settings.set("monitoring_paused", "0")
        await message.answer(
            "▶️ Мониторинг запущен.", reply_markup=kb.main_menu_kb(monitoring_paused=False)
        )
        return

    await state.set_state(ResumeStates.confirming)
    await message.answer(
        f"⏸ Пауза длилась {format_pause_duration(duration)}. Догнать пропущенное? [Да] [Нет]",
        reply_markup=kb.resume_confirm_kb(),
    )


async def on_resume_confirm(
    callback: CallbackQuery,
    callback_data: kb.ResumeConfirmCB,
    state: FSMContext,
    sources: store.SourcesRepo,
    receivers: store.ReceiversRepo,
    keywords: store.KeywordsRepo,
    hits: store.HitsRepo,
    delivery_queue: store.DeliveryQueueRepo,
    settings: store.SettingsRepo,
    tg_client,
) -> None:
    await state.clear()
    if callback_data.answer == "yes":
        duration = await _pause_duration_seconds(settings)

        ruleset = await build_ruleset(keywords)
        for source in await sources.list():
            if source.paused:
                continue
            try:
                await userbot_catchup.scan_source(
                    tg_client,
                    source,
                    sources=sources,
                    receivers=receivers,
                    hits=hits,
                    delivery_queue=delivery_queue,
                    settings=settings,
                    ruleset_provider=lambda: ruleset,
                )
            except userbot_catchup.INACCESSIBLE_SOURCE_ERRORS:
                await sources.update(source.id, paused=True)
        await settings.set("monitoring_paused", "0")
        text = "▶️ Мониторинг запущен, догоняю пропущенное…"

        scan_days = int(await settings.get("scan_days", "3"))
        if duration is not None and duration > scan_days * 86400:
            text += (
                f"\n⚠️ Пауза была длиннее предохранителя ({scan_days} дн.) — "
                "самые старые пропущенные сообщения могли не долететь."
            )
    else:
        await settings.set("monitoring_paused", "0")
        text = "▶️ Мониторинг запущен."
    await callback.message.edit_text(text)
    await callback.answer()


def build_router() -> Router:
    router = Router(name="panel.stats")

    router.message.register(on_status, F.text == kb.BTN_STATUS)
    router.message.register(on_stats_menu, F.text == kb.BTN_STATS)
    router.message.register(on_top_keywords, F.text == kb.BTN_TOP)
    router.message.register(on_history, F.text == kb.BTN_HISTORY)
    router.callback_query.register(on_stats_period, kb.StatsPeriodCB.filter())

    router.message.register(on_toggle_monitoring, F.text.in_({kb.BTN_PAUSE_ON, kb.BTN_PAUSE_OFF}))
    router.callback_query.register(
        on_resume_confirm, kb.ResumeConfirmCB.filter(), ResumeStates.confirming
    )

    return router
