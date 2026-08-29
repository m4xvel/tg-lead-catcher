"""Клавиатуры и callback_data-схемы панели: чистые билдеры, без побочных эффектов.

Никаких обращений к `store`/`userbot` — только aiogram-разметка, поэтому построенные
клавиатуры проверяются без БД и без сети. `panel.sources`/`panel.receivers`
используют эти билдеры и объявленные здесь `CallbackData`-схемы для разбора нажатий;
`target` в схемах различает источники (R41) и приёмники (R42), которые ведут себя
почти одинаково.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

TARGET_SOURCE = "source"
TARGET_RECEIVER = "receiver"

PAGE_SIZE = 8  # тот же размер страницы, что и «Из моих чатов» (R38) — для единообразия

BTN_SOURCES = "📂 Источники"
BTN_RECEIVERS = "📥 Приёмники"
BTN_PAUSE_ON = "⏸ Остановить мониторинг"
BTN_PAUSE_OFF = "▶️ Запустить мониторинг"
BTN_ADD = "➕ Добавить чат"
BTN_FAVORITE = "💾 Избранное"
BTN_FROM_DIALOGS = "📋 Из моих чатов"
BTN_FORWARD = "↩️ Переслать сообщение"
BTN_MANUAL = "⌨️ Ввести вручную"
BTN_CANCEL = "‹ Отмена"
BTN_SEARCH = "🔍 Искать"
BTN_SCAN = "🔍 Просканировать 7 дней"
BTN_PREV = "‹"
BTN_NEXT = "›"
NOOP = "noop"

# --- тикет 05: ключи/минус-слова, статус/статистика/история ------------------
TARGET_KEYWORD = "keyword"
TARGET_STOPWORD = "stopword"

BTN_KEYWORDS = "🔑 Ключевые слова"
BTN_STOPWORDS = "🚫 Минус-слова"
BTN_STATUS = "📈 Статус"
BTN_STATS = "📊 Статистика"
BTN_TOP = "📊 Топ мусорных ключей"
BTN_HISTORY = "🕐 История"
BTN_ADD_KEYWORDS = "➕ Добавить"
BTN_CHECK = "🧪 Проверить"
BTN_YES = "Да"
BTN_NO = "Нет"
BTN_STATS_7D = "7 дней"
BTN_STATS_30D = "30 дней"

# --- тикет 09: настройки ------------------------------------------------------
BTN_SETTINGS = "⚙️ Настройки"

SETTINGS_BOOL_LABELS = {
    "monitor_all_dm": "👥 Личные чаты",
    "dedup_enabled": "🔁 Дедуп по автору и тексту",
}

# ключ -> (подпись, суффикс единицы измерения для отображения в кнопке)
SETTINGS_NUMERIC_LABELS = {
    "dedup_window_days": ("🔁 Окно дедупа", "дн."),
    "scan_days": ("🔍 Глубина догона (дни)", "дн."),
    "scan_msgs": ("🔍 Глубина догона (сообщения)", "сообщ."),
    "rate_limit_per_min": ("📤 Лимит отправки", "/мин"),
    "retention_days": ("🗑 Ретеншн", "дн."),
}


class SettingsToggleCB(CallbackData, prefix="stg"):
    key: str


class SettingsEditCB(CallbackData, prefix="sed"):
    key: str


class CheckStartCB(CallbackData, prefix="chk"):
    pass


class ResumeConfirmCB(CallbackData, prefix="rsc"):
    answer: str  # yes | no


class StatsPeriodCB(CallbackData, prefix="sp"):
    days: int


class AddMethodCB(CallbackData, prefix="am"):
    target: str
    method: str  # dialogs | forward | manual


class CancelAddCB(CallbackData, prefix="cnl"):
    target: str


class DialogPageCB(CallbackData, prefix="dp"):
    target: str
    offset: int


class DialogSearchCB(CallbackData, prefix="ds"):
    target: str


class DialogPickCB(CallbackData, prefix="dk"):
    target: str
    chat_id: int


class ListPageCB(CallbackData, prefix="lp"):
    target: str
    offset: int


class AddStartCB(CallbackData, prefix="as"):
    target: str


class DeleteCB(CallbackData, prefix="rm"):
    target: str
    id: int


class PauseToggleCB(CallbackData, prefix="pz"):
    id: int


class ScanCB(CallbackData, prefix="sn"):
    id: int


class FavoriteAddCB(CallbackData, prefix="fav"):
    pass


def truncate(text: str, limit: int = 40) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main_menu_kb(*, monitoring_paused: bool) -> ReplyKeyboardMarkup:
    """Постоянная нижняя клавиатура: разделы + глобальный тумблер мониторинга (R48)."""
    toggle = BTN_PAUSE_OFF if monitoring_paused else BTN_PAUSE_ON
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SOURCES), KeyboardButton(text=BTN_RECEIVERS)],
            [KeyboardButton(text=toggle)],
            # Тикет 05: разделы ключей/статуса/статистики/истории — новая строка,
            # существующие ряды выше не тронуты.
            [KeyboardButton(text=BTN_KEYWORDS), KeyboardButton(text=BTN_STOPWORDS)],
            [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_STATS)],
            [KeyboardButton(text=BTN_TOP), KeyboardButton(text=BTN_HISTORY)],
            # Тикет 09: раздел настроек — новая строка, существующие не тронуты.
            [KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
    )


def add_method_kb(target: str) -> InlineKeyboardMarkup:
    """Три способа добавления чата (R38-R40) + отмена."""
    b = InlineKeyboardBuilder()
    b.button(text=BTN_FROM_DIALOGS, callback_data=AddMethodCB(target=target, method="dialogs"))
    b.button(text=BTN_FORWARD, callback_data=AddMethodCB(target=target, method="forward"))
    b.button(text=BTN_MANUAL, callback_data=AddMethodCB(target=target, method="manual"))
    b.button(text=BTN_CANCEL, callback_data=CancelAddCB(target=target))
    b.adjust(1)
    return b.as_markup()


def cancel_kb(target: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=BTN_CANCEL, callback_data=CancelAddCB(target=target))
    b.adjust(1)
    return b.as_markup()


def dialogs_page_kb(target: str, dialogs, offset: int, *, has_more: bool) -> InlineKeyboardMarkup:
    """Страница «Из моих чатов» (R38): кнопка на диалог + пагинация + поиск."""
    b = InlineKeyboardBuilder()
    for d in dialogs:
        b.row(
            InlineKeyboardButton(
                text=truncate(d.title),
                callback_data=DialogPickCB(target=target, chat_id=d.chat_id).pack(),
            )
        )
    nav = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text=BTN_PREV,
                callback_data=DialogPageCB(
                    target=target, offset=max(0, offset - PAGE_SIZE)
                ).pack(),
            )
        )
    if has_more:
        nav.append(
            InlineKeyboardButton(
                text=BTN_NEXT,
                callback_data=DialogPageCB(target=target, offset=offset + PAGE_SIZE).pack(),
            )
        )
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text=BTN_SEARCH, callback_data=DialogSearchCB(target=target).pack()))
    b.row(InlineKeyboardButton(text=BTN_CANCEL, callback_data=CancelAddCB(target=target).pack()))
    return b.as_markup()


def list_page_kb(
    target: str,
    items,
    offset: int,
    *,
    page_size: int = PAGE_SIZE,
    with_pause: bool,
    show_favorite: bool = False,
) -> InlineKeyboardMarkup:
    """Постраничный список источников/приёмников с ❌ и (для источников) паузой (R41/R42)."""
    b = InlineKeyboardBuilder()
    page = items[offset : offset + page_size]
    for item in page:
        if with_pause:
            label = ("▶️ " if item.paused else "⏸ ") + truncate(item.title, 28)
            toggle_cb = PauseToggleCB(id=item.id).pack()
        else:
            label = truncate(item.title, 28)
            toggle_cb = NOOP
        b.row(
            InlineKeyboardButton(text=label, callback_data=toggle_cb),
            InlineKeyboardButton(
                text="❌", callback_data=DeleteCB(target=target, id=item.id).pack()
            ),
        )
        if with_pause:
            # Разовое сканирование истории за 7 дней (R36) — только у источников.
            b.row(
                InlineKeyboardButton(text=BTN_SCAN, callback_data=ScanCB(id=item.id).pack())
            )
    nav = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text=BTN_PREV,
                callback_data=ListPageCB(target=target, offset=max(0, offset - page_size)).pack(),
            )
        )
    if offset + page_size < len(items):
        nav.append(
            InlineKeyboardButton(
                text=BTN_NEXT,
                callback_data=ListPageCB(target=target, offset=offset + page_size).pack(),
            )
        )
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text=BTN_ADD, callback_data=AddStartCB(target=target).pack()))
    if show_favorite:
        b.row(InlineKeyboardButton(text=BTN_FAVORITE, callback_data=FavoriteAddCB().pack()))
    return b.as_markup()


def keyword_list_kb(target: str, items, offset: int = 0, *, page_size: int = PAGE_SIZE) -> InlineKeyboardMarkup:
    """Список ключей/минус-слов (R44-R47): ❌ на каждый, построчное добавление, проверка."""
    b = InlineKeyboardBuilder()
    page = items[offset : offset + page_size]
    for item in page:
        b.row(
            InlineKeyboardButton(text=truncate(item.pattern, 32), callback_data=NOOP),
            InlineKeyboardButton(
                text="❌", callback_data=DeleteCB(target=target, id=item.id).pack()
            ),
        )
    nav = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text=BTN_PREV,
                callback_data=ListPageCB(target=target, offset=max(0, offset - page_size)).pack(),
            )
        )
    if offset + page_size < len(items):
        nav.append(
            InlineKeyboardButton(
                text=BTN_NEXT,
                callback_data=ListPageCB(target=target, offset=offset + page_size).pack(),
            )
        )
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text=BTN_ADD_KEYWORDS, callback_data=AddStartCB(target=target).pack()))
    b.row(InlineKeyboardButton(text=BTN_CHECK, callback_data=CheckStartCB().pack()))
    return b.as_markup()


def resume_confirm_kb() -> InlineKeyboardMarkup:
    """Вопрос о догоне при снятии глобальной паузы (R53): [Да]/[Нет]."""
    b = InlineKeyboardBuilder()
    b.button(text=BTN_YES, callback_data=ResumeConfirmCB(answer="yes"))
    b.button(text=BTN_NO, callback_data=ResumeConfirmCB(answer="no"))
    b.adjust(2)
    return b.as_markup()


def stats_period_kb(days: int) -> InlineKeyboardMarkup:
    """Переключатель периода статистики (R50): 7/30 дней."""
    b = InlineKeyboardBuilder()
    b.button(text=BTN_STATS_7D, callback_data=StatsPeriodCB(days=7))
    b.button(text=BTN_STATS_30D, callback_data=StatsPeriodCB(days=30))
    b.adjust(2)
    return b.as_markup()


def settings_menu_kb(values: dict[str, str]) -> InlineKeyboardMarkup:
    """Список из шести настроек (R26/R31/R33/R37/R62/R68): тумблеры переключаются
    сразу этой же кнопкой, числовые/текстовые поля открывают ввод нового значения.
    Каждая кнопка показывает текущее значение (критерий приёмки тикета 09).
    """
    b = InlineKeyboardBuilder()

    on = values.get("monitor_all_dm", "0") == "1"
    b.row(
        InlineKeyboardButton(
            text=f"{SETTINGS_BOOL_LABELS['monitor_all_dm']}: {'вкл' if on else 'выкл'}",
            callback_data=SettingsToggleCB(key="monitor_all_dm").pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text="✍️ Шаблон подписи", callback_data=SettingsEditCB(key="card_template").pack()
        )
    )

    label, unit = SETTINGS_NUMERIC_LABELS["dedup_window_days"]
    b.row(
        InlineKeyboardButton(
            text=f"{label}: {values.get('dedup_window_days', '')} {unit}",
            callback_data=SettingsEditCB(key="dedup_window_days").pack(),
        )
    )
    dedup_on = values.get("dedup_enabled", "1") == "1"
    b.row(
        InlineKeyboardButton(
            text=f"{SETTINGS_BOOL_LABELS['dedup_enabled']}: {'вкл' if dedup_on else 'выкл'}",
            callback_data=SettingsToggleCB(key="dedup_enabled").pack(),
        )
    )

    for key in ("scan_days", "scan_msgs", "rate_limit_per_min", "retention_days"):
        label, unit = SETTINGS_NUMERIC_LABELS[key]
        b.row(
            InlineKeyboardButton(
                text=f"{label}: {values.get(key, '')} {unit}",
                callback_data=SettingsEditCB(key=key).pack(),
            )
        )

    return b.as_markup()
