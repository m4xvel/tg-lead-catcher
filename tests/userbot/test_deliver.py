"""userbot.deliver — форвард/копия/карточка (R27-R31), фейковый Telethon-клиент,
без реальной отправки в Telegram (interfaces.md)."""
from __future__ import annotations

from telethon.errors import ChatForwardsRestrictedError

from store import Hit, Receiver, Source
from tests.conftest import FakeEntity, FakeMessage
from userbot.deliver import deliver

RECEIVER = Receiver(id=1, chat_id=-100999, title="Приёмник", added_at="2026-08-28 10:00:00")


def _hit(**overrides) -> Hit:
    base = dict(
        id=1,
        source_chat_id=-100111,
        message_id=555,
        author_id=123456789,
        author_username="ivan_p",
        author_name="Иван П.",
        matched_keywords=["ремонт", "срочно"],
        text_hash="hash",
        text_preview="нужен ремонт срочно",
        also_in=[],
        created_at="2026-08-27 14:32:00",
    )
    base.update(overrides)
    return Hit(**base)


def _source(**overrides) -> Source:
    base = dict(
        id=1,
        chat_id=-100111,
        title="Ремонт СПб",
        kind="supergroup",
        paused=False,
        last_processed_msg_id=None,
        added_at="2026-08-27 10:00:00",
    )
    base.update(overrides)
    return Source(**base)


async def test_forward_success_sends_card_as_second_message(fake_client):
    hit = _hit()
    source = _source()

    result = await deliver(
        fake_client, hit, source, RECEIVER,
        card_template="🔑 {keywords}\n👤 {author}\n💬 {chat}\n🕐 {time}",
    )

    assert result.ok is True
    assert result.used_copy_fallback is False
    # первый вызов — форвард, второй (и последний) — карточка отдельным сообщением
    assert fake_client.calls[0][0] == "forward"
    assert fake_client.calls[-1][0] == "send_message"
    card_text = fake_client.calls[-1][2]
    assert "ремонт, срочно" in card_text


async def test_forward_restricted_falls_back_to_copy_then_card(fake_client):
    hit = _hit()
    source = _source()
    fake_client.forward_error = ChatForwardsRestrictedError(request=None)
    fake_client.messages[hit.message_id] = FakeMessage("оригинальный текст сообщения")

    result = await deliver(
        fake_client, hit, source, RECEIVER,
        card_template="🔑 {keywords}\n👤 {author}\n💬 {chat}\n🕐 {time}",
    )

    assert result.ok is True
    assert result.used_copy_fallback is True
    send_calls = [c for c in fake_client.calls if c[0] == "send_message"]
    assert len(send_calls) == 2  # копия текста + карточка
    assert send_calls[0][2] == "оригинальный текст сообщения"
    assert "ремонт" in send_calls[1][2]


async def test_card_has_tg_link_and_numeric_id_when_no_username(fake_client):
    hit = _hit(author_username=None, author_id=987654321)
    source = _source()

    await deliver(
        fake_client, hit, source, RECEIVER,
        card_template="👤 {author}",
    )

    card_text = fake_client.calls[-1][2]
    assert "tg://user?id=987654321" in card_text
    assert "987654321" in card_text


async def test_original_link_is_public_for_public_chat(fake_client):
    hit = _hit()
    source = _source(chat_id=-100222)
    fake_client.entities[-100222] = FakeEntity("remont_spb")

    await deliver(fake_client, hit, source, RECEIVER, card_template="💬 {chat}")

    card_text = fake_client.calls[-1][2]
    assert f"https://t.me/remont_spb/{hit.message_id}" in card_text


async def test_original_link_is_private_for_chat_without_username(fake_client):
    hit = _hit()
    source = _source(chat_id=-100777888)  # без username в fake_client.entities

    await deliver(fake_client, hit, source, RECEIVER, card_template="💬 {chat}")

    card_text = fake_client.calls[-1][2]
    assert f"https://t.me/c/777888/{hit.message_id}" in card_text


async def test_card_template_change_affects_next_card(fake_client):
    hit = _hit()
    source = _source()

    await deliver(fake_client, hit, source, RECEIVER, card_template="Шаблон-раз: {keywords}")
    first_card = fake_client.calls[-1][2]

    await deliver(fake_client, hit, source, RECEIVER, card_template="Шаблон-два: {keywords}")
    second_card = fake_client.calls[-1][2]

    assert "Шаблон-раз" in first_card
    assert "Шаблон-два" in second_card
    assert first_card != second_card
