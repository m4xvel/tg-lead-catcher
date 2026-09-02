"""`/export` (R55): дамп источников, ключей, минус-слов и приёмников одним JSON —
чтобы при переезде на VPS не набивать списки заново.

`build_router()` — фабрика (см. докстринг `panel.sources`): свежий `Router` на
каждый вызов.
"""
from __future__ import annotations

import json

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message

import store

EXPORT_FILENAME = "tg-lead-catcher-export.json"


async def build_export_payload(
    *, sources: store.SourcesRepo, keywords: store.KeywordsRepo, receivers: store.ReceiversRepo
) -> dict:
    all_sources = await sources.list()
    all_keywords = await keywords.list(is_stop=False)
    all_stopwords = await keywords.list(is_stop=True)
    all_receivers = await receivers.list()
    return {
        "sources": [
            {"chat_id": s.chat_id, "title": s.title, "kind": s.kind, "paused": s.paused}
            for s in all_sources
        ],
        "keywords": [{"kind": k.kind, "pattern": k.pattern} for k in all_keywords],
        "stopwords": [{"kind": k.kind, "pattern": k.pattern} for k in all_stopwords],
        "receivers": [{"chat_id": r.chat_id, "title": r.title} for r in all_receivers],
    }


async def cmd_export(
    message: Message,
    state: FSMContext,
    sources: store.SourcesRepo,
    keywords: store.KeywordsRepo,
    receivers: store.ReceiversRepo,
) -> None:
    # Тикет 11 (G01/G4): `/export` — команда без фильтра состояния, роутер
    # подключён раньше `settings`/`sources`/`receivers` (см. `panel/__init__.py`)
    # — перехватывает апдейт первым, даже когда FSM ждёт свободный текст
    # (например `SettingsStates.waiting_value`). Экспорт по-прежнему реально
    # отправляется, но незавершённое ожидание нужно тихо снять — тот же
    # принцип, что и в `panel.sources.cmd_start`.
    await state.clear()
    payload = await build_export_payload(sources=sources, keywords=keywords, receivers=receivers)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    document = BufferedInputFile(body, filename=EXPORT_FILENAME)
    await message.answer_document(document, caption="📦 Экспорт настроек")


def build_router() -> Router:
    router = Router(name="panel.export")
    router.message.register(cmd_export, Command("export"))
    return router
