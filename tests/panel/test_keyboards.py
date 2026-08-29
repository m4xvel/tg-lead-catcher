"""Клавиатуры — чистые билдеры (без store/aiogram-сети), см. panel/keyboards.py."""
from __future__ import annotations

from dataclasses import dataclass

from panel import keyboards as kb


@dataclass
class _Item:
    id: int
    title: str
    paused: bool = False


def test_main_menu_shows_correct_toggle_label_depending_on_state():
    running = kb.main_menu_kb(monitoring_paused=False)
    paused = kb.main_menu_kb(monitoring_paused=True)

    running_texts = [b.text for row in running.keyboard for b in row]
    paused_texts = [b.text for row in paused.keyboard for b in row]

    assert kb.BTN_PAUSE_ON in running_texts
    assert kb.BTN_PAUSE_OFF in paused_texts
    assert kb.BTN_SOURCES in running_texts
    assert kb.BTN_RECEIVERS in running_texts


def test_add_method_kb_offers_exactly_three_methods_plus_cancel():
    markup = kb.add_method_kb(kb.TARGET_SOURCE)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert texts == [kb.BTN_FROM_DIALOGS, kb.BTN_FORWARD, kb.BTN_MANUAL, kb.BTN_CANCEL]


def test_list_page_kb_source_row_has_pause_toggle_and_delete():
    items = [_Item(id=1, title="Чат про ремонт", paused=False)]
    markup = kb.list_page_kb(kb.TARGET_SOURCE, items, offset=0, with_pause=True)
    row = markup.inline_keyboard[0]
    assert row[0].text.startswith("⏸")
    assert row[1].text == "❌ удалить"
    cb = kb.PauseToggleCB.unpack(row[0].callback_data)
    assert cb.id == 1
    del_cb = kb.DeleteCB.unpack(row[1].callback_data)
    assert del_cb.id == 1 and del_cb.target == kb.TARGET_SOURCE


def test_list_page_kb_receiver_has_no_pause_button_but_has_favorite():
    items = [_Item(id=5, title="Мой чат")]
    markup = kb.list_page_kb(
        kb.TARGET_RECEIVER, items, offset=0, with_pause=False, show_favorite=True
    )
    row = markup.inline_keyboard[0]
    assert row[0].callback_data == kb.NOOP
    last_row = markup.inline_keyboard[-1]
    assert last_row[0].text == kb.BTN_FAVORITE


def test_list_page_kb_pagination_next_button_appears_only_when_more_items():
    items = [_Item(id=i, title=str(i)) for i in range(10)]
    first_page = kb.list_page_kb(kb.TARGET_SOURCE, items, offset=0, page_size=8, with_pause=False)
    last_row_texts = [b.text for b in first_page.inline_keyboard[-2]]
    assert kb.BTN_NEXT in last_row_texts
    assert kb.BTN_PREV not in last_row_texts

    second_page = kb.list_page_kb(kb.TARGET_SOURCE, items, offset=8, page_size=8, with_pause=False)
    nav_texts = [b.text for b in second_page.inline_keyboard[-2]]
    assert kb.BTN_PREV in nav_texts
    assert kb.BTN_NEXT not in nav_texts
