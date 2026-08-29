"""Раздел «⚙️ Настройки» (R26/R31/R33/R37/R62/R68): редактирование шести уже
существующих ключей `settings` — до этого тикета все они читались кодом
(`userbot`/`worker`), но менять их можно было только прямой записью в БД.

Каждое изменение сразу пишет в `settings` через `SettingsRepo.update` (ключи
уже существуют в схеме — `store.db.DEFAULT_SETTINGS` досевает их при
`create_schema`, так что `update` никогда не находит пустое место) и отвечает
коротким подтверждением («✅ Сохранено: 14 дней»).

`build_router()` — фабрика (см. докстринг `panel.sources` — почему не
модульный синглтон): свежий `Router` на каждый вызов.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import store
from userbot.catchup import HARD_MSG_CAP

from . import keyboards as kb

BOOL_KEYS = {"monitor_all_dm", "dedup_enabled"}
TEXT_KEYS = {"card_template"}
NUMERIC_KEYS = {"dedup_window_days", "scan_days", "scan_msgs", "rate_limit_per_min", "retention_days"}

# Подсказка при входе в редактирование (показывает единицу измерения и
# ограничение) — текущее значение подставляется отдельно, перед этим текстом.
NUMERIC_PROMPTS: dict[str, str] = {
    "dedup_window_days": "Введите окно дедупа в днях (целое число больше 0).",
    "scan_days": "Введите глубину догона в днях (целое число больше 0).",
    "scan_msgs": f"Введите глубину догона в сообщениях (целое число от 1 до {HARD_MSG_CAP}).",
    "rate_limit_per_min": "Введите лимит отправки, сообщений в минуту (целое число больше 0).",
    "retention_days": "Введите срок ретеншна в днях (целое число больше 0).",
}

# Суффикс подтверждения («✅ Сохранено: 14 дней») — точный вид из тикета.
NUMERIC_CONFIRM_SUFFIX: dict[str, str] = {
    "dedup_window_days": " дней",
    "scan_days": " дней",
    "scan_msgs": " сообщений",
    "rate_limit_per_min": "/мин",
    "retention_days": " дней",
}

CARD_TEMPLATE_PLACEHOLDERS_HINT = (
    "Доступные плейсхолдеры: {keywords}, {author}, {chat}, {time}."
)


class SettingsStates(StatesGroup):
    waiting_value = State()


def validate_int(raw: str, key: str) -> tuple[int | None, str | None]:
    """Разбирает числовой ввод настройки (R33/R37/R62/R68): нечисловой,
    отрицательный или нулевой ввод — понятная русская ошибка, не падение.
    `scan_msgs` дополнительно зажат `HARD_MSG_CAP` (R37) — выше явно отклоняется.
    """
    text = (raw or "").strip()
    try:
        value = int(text)
    except ValueError:
        return None, "❌ Нужно целое число."
    if value <= 0:
        return None, "❌ Число должно быть больше нуля."
    if key == "scan_msgs" and value > HARD_MSG_CAP:
        return None, f"❌ Максимум {HARD_MSG_CAP} сообщений за раз."
    return value, None


def format_saved_numeric(key: str, value: int) -> str:
    return f"✅ Сохранено: {value}{NUMERIC_CONFIRM_SUFFIX.get(key, '')}"


async def _load_values(settings: store.SettingsRepo) -> dict[str, str]:
    return await settings.list()


async def _render_menu(event: Message | CallbackQuery, settings: store.SettingsRepo) -> None:
    values = await _load_values(settings)
    text = "⚙️ Настройки. Нажмите пункт, чтобы изменить значение."
    markup = kb.settings_menu_kb(values)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


# --- хендлеры --------------------------------------------------------------


async def cmd_settings_menu(message: Message, state: FSMContext, settings: store.SettingsRepo) -> None:
    await state.clear()
    await _render_menu(message, settings)


async def on_settings_toggle(
    callback: CallbackQuery, callback_data: kb.SettingsToggleCB, settings: store.SettingsRepo
) -> None:
    """Тумблеры `monitor_all_dm`/`dedup_enabled` (R26/R33) — переключаются одним
    нажатием, без отдельного подтверждения (сама кнопка меняет подпись)."""
    key = callback_data.key
    current = (await settings.get(key, "0")) == "1"
    await settings.update(key, "0" if current else "1")
    await _render_menu(callback, settings)
    await callback.answer("✅ Сохранено")


async def on_settings_edit_start(
    callback: CallbackQuery,
    callback_data: kb.SettingsEditCB,
    state: FSMContext,
    settings: store.SettingsRepo,
) -> None:
    """Показывает текущее значение перед вводом нового (критерий приёмки тикета
    09) и переводит FSM в ожидание текста."""
    key = callback_data.key
    await state.set_state(SettingsStates.waiting_value)
    await state.update_data(key=key)
    if key == "card_template":
        current = await settings.get(key, "")
        await callback.message.edit_text(
            f"Текущий шаблон подписи:\n{current}\n\n"
            f"Пришлите новый шаблон. {CARD_TEMPLATE_PLACEHOLDERS_HINT}"
        )
    else:
        current = await settings.get(key, "")
        await callback.message.edit_text(
            f"Текущее значение: {current}.\n{NUMERIC_PROMPTS[key]}"
        )
    await callback.answer()


async def on_settings_value(message: Message, state: FSMContext, settings: store.SettingsRepo) -> None:
    data = await state.get_data()
    key = data.get("key")
    await state.clear()
    if key is None:
        return

    if key == "card_template":
        template = message.text or ""
        if not template.strip():
            await message.answer("❌ Шаблон не может быть пустым.")
            return
        await settings.update(key, template)
        await message.answer("✅ Сохранено: новый шаблон подписи.")
    else:
        value, error = validate_int(message.text or "", key)
        if error is not None:
            await message.answer(error)
            return
        await settings.update(key, str(value))
        await message.answer(format_saved_numeric(key, value))

    await _render_menu(message, settings)


def build_router() -> Router:
    router = Router(name="panel.settings")

    router.message.register(cmd_settings_menu, F.text == kb.BTN_SETTINGS)
    router.callback_query.register(on_settings_toggle, kb.SettingsToggleCB.filter())
    router.callback_query.register(on_settings_edit_start, kb.SettingsEditCB.filter())
    router.message.register(on_settings_value, SettingsStates.waiting_value)

    return router
