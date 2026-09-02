"""Ключевые слова и минус-слова (R04, R44-R47, R79i): построчный ввод с отсечением
дублей, ❌ без подтверждения, «🧪 Проверить» через `matcher` без сохранения.

Разбор сырого построчного ввода (`re:` префикс, кавычки для фразы) — забота
панели, не `matcher` (interfaces.md, тикет 01): `parse_keyword_line` здесь.

`build_router()` — фабрика (см. докстринг `panel.sources` — почему не
модульный синглтон): свежий `Router` на каждый вызов.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import matcher
import store

from . import keyboards as kb

_TARGETS = {kb.TARGET_KEYWORD: False, kb.TARGET_STOPWORD: True}


class KeywordStates(StatesGroup):
    waiting_bulk = State()
    waiting_check_text = State()


def parse_keyword_line(line: str) -> tuple[str, str] | None:
    """Разбирает одну строку ввода в `(kind, pattern)` (R18/R19). Пустая строка — None."""
    text = line.strip()
    if not text:
        return None
    if text.startswith("re:"):
        return ("regex", text[3:].strip())
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return ("phrase", text[1:-1].strip())
    return ("word", text)


def _dedup_key(kind: str, pattern: str) -> tuple[str, str]:
    """Ключ для отсечения дублей (R44): регистр/ё-е не различаем для word/phrase,
    как и сам матчинг (R20); regex сравниваем как есть — синтаксис регистрозависим."""
    if kind == "regex":
        return (kind, pattern)
    normalized = pattern.translate(str.maketrans({"ё": "е", "Ё": "Е"})).casefold()
    return (kind, normalized)


@dataclass
class AddResult:
    added: list[store.Keyword] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def add_keyword_lines(text: str, keywords: store.KeywordsRepo, *, is_stop: bool) -> AddResult:
    """Построчный ввод (R44/R45): до N новых ключей нужного типа, дубли молча
    отсекаются (в т.ч. дубли внутри самого вставленного текста)."""
    existing = await keywords.list(is_stop=is_stop)
    seen = {_dedup_key(k.kind, k.pattern) for k in existing}
    result = AddResult()
    for raw_line in (text or "").splitlines():
        parsed = parse_keyword_line(raw_line)
        if parsed is None:
            continue
        kind, pattern = parsed
        if not pattern:
            continue
        key = _dedup_key(kind, pattern)
        if key in seen:
            continue
        try:
            added = await keywords.add(kind, pattern, is_stop=is_stop)
        except matcher.InvalidKeywordError as exc:
            result.errors.append(str(exc))
            continue
        seen.add(key)
        result.added.append(added)
    return result


def format_add_result(result: AddResult, *, is_stop: bool) -> str:
    label = "минус-слов" if is_stop else "ключей"
    lines = [f"✅ Добавлено {label}: {len(result.added)}."]
    for error in result.errors:
        lines.append(f"❌ {error}")
    return "\n".join(lines)


async def build_ruleset(keywords: store.KeywordsRepo) -> matcher.Ruleset:
    kws = await keywords.list(is_stop=False)
    stops = await keywords.list(is_stop=True)
    return matcher.compile(
        [(k.kind, k.pattern) for k in kws],
        [(k.kind, k.pattern) for k in stops],
    )


def format_check_result(result: matcher.MatchResult) -> str:
    """Один из трёх точных текстов из брифа (R47)."""
    if result.blocked_by is not None:
        return f"⛔ заблокировано минус-словом: {result.blocked_by}"
    if result.matched:
        return f"✅ сработало по: {', '.join(result.hit_keywords)}"
    return "❌ не сработало"


# --- хендлеры ------------------------------------------------------------


async def render_keyword_list(
    event: Message | CallbackQuery,
    state: FSMContext,
    *,
    target: str,
    keywords: store.KeywordsRepo,
    offset: int = 0,
) -> None:
    await state.clear()
    is_stop = _TARGETS[target]
    items = await keywords.list(is_stop=is_stop)
    label = "Минус-слова" if is_stop else "Ключевые слова"
    emoji = "🚫" if is_stop else "🔑"
    text = f"{emoji} {label}:" if items else f"{label}: список пуст."
    markup = kb.keyword_list_kb(target, items, offset)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


async def cmd_keywords_menu(message: Message, state: FSMContext, keywords: store.KeywordsRepo) -> None:
    await render_keyword_list(message, state, target=kb.TARGET_KEYWORD, keywords=keywords)


async def cmd_stopwords_menu(message: Message, state: FSMContext, keywords: store.KeywordsRepo) -> None:
    await render_keyword_list(message, state, target=kb.TARGET_STOPWORD, keywords=keywords)


async def on_list_page(
    callback: CallbackQuery, callback_data: kb.ListPageCB, state: FSMContext, keywords: store.KeywordsRepo
) -> None:
    await render_keyword_list(
        callback, state, target=callback_data.target, keywords=keywords, offset=callback_data.offset
    )


async def on_delete(
    callback: CallbackQuery, callback_data: kb.DeleteCB, state: FSMContext, keywords: store.KeywordsRepo
) -> None:
    """❌ удаляет немедленно, без подтверждения (R79i/R46)."""
    await keywords.remove(callback_data.id)
    await render_keyword_list(callback, state, target=callback_data.target, keywords=keywords)


async def on_add_start(callback: CallbackQuery, callback_data: kb.AddStartCB, state: FSMContext) -> None:
    is_stop = _TARGETS[callback_data.target]
    label = "минус-слов" if is_stop else "ключевых слов"
    await state.set_state(KeywordStates.waiting_bulk)
    await state.update_data(target=callback_data.target)
    await callback.message.edit_text(
        f"Пришлите список {label} — каждый на своей строке.\n"
        'Слово — как есть, точная фраза — в "кавычках", регулярка — с префиксом re:',
        reply_markup=kb.keyword_cancel_kb(callback_data.target),
    )
    await callback.answer()


async def on_keyword_cancel(
    callback: CallbackQuery,
    callback_data: kb.KeywordCancelCB,
    state: FSMContext,
    keywords: store.KeywordsRepo,
) -> None:
    """Тикет 11 (G02): тихая отмена построчного ввода — тот же список, что и
    после обычного добавления."""
    await render_keyword_list(callback, state, target=callback_data.target, keywords=keywords)


async def on_bulk_text(message: Message, state: FSMContext, keywords: store.KeywordsRepo) -> None:
    data = await state.get_data()
    target = data.get("target", kb.TARGET_KEYWORD)
    if (message.text or "").startswith("/"):
        # Тикет 11 (G01): команда во время ожидания ввода = тихая отмена.
        await render_keyword_list(message, state, target=target, keywords=keywords)
        return
    is_stop = _TARGETS[target]
    result = await add_keyword_lines(message.text or "", keywords, is_stop=is_stop)
    await message.answer(format_add_result(result, is_stop=is_stop))
    await render_keyword_list(message, state, target=target, keywords=keywords)


async def on_check_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(KeywordStates.waiting_check_text)
    await callback.message.edit_text("Пришлите текст для проверки.", reply_markup=kb.check_cancel_kb())
    await callback.answer()


async def on_check_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Тикет 11 (G02): просто прерывает ожидание ввода, списка для возврата нет."""
    await state.clear()
    await callback.answer()


async def on_check_text(message: Message, state: FSMContext, keywords: store.KeywordsRepo) -> None:
    if (message.text or "").startswith("/"):
        # Тикет 11 (G01): команда во время ожидания ввода = тихая отмена.
        await state.clear()
        return
    await state.clear()
    ruleset = await build_ruleset(keywords)
    result = ruleset.match(message.text or "")
    await message.answer(format_check_result(result))


def build_router() -> Router:
    router = Router(name="panel.keywords")

    router.message.register(cmd_keywords_menu, F.text == kb.BTN_KEYWORDS)
    router.message.register(cmd_stopwords_menu, F.text == kb.BTN_STOPWORDS)

    router.callback_query.register(
        on_list_page,
        kb.ListPageCB.filter(F.target.in_({kb.TARGET_KEYWORD, kb.TARGET_STOPWORD})),
    )
    router.callback_query.register(
        on_delete,
        kb.DeleteCB.filter(F.target.in_({kb.TARGET_KEYWORD, kb.TARGET_STOPWORD})),
    )
    router.callback_query.register(
        on_add_start,
        kb.AddStartCB.filter(F.target.in_({kb.TARGET_KEYWORD, kb.TARGET_STOPWORD})),
    )
    router.callback_query.register(
        on_keyword_cancel,
        kb.KeywordCancelCB.filter(F.target.in_({kb.TARGET_KEYWORD, kb.TARGET_STOPWORD})),
    )
    router.message.register(on_bulk_text, KeywordStates.waiting_bulk)

    router.callback_query.register(on_check_start, kb.CheckStartCB.filter())
    router.callback_query.register(on_check_cancel, kb.CheckCancelCB.filter())
    router.message.register(on_check_text, KeywordStates.waiting_check_text)

    return router
