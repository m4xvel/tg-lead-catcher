"""Тесты для setup.py: парсинг/запись .env и логин userbot с фейковым Telethon-клиентом.

Интерактивный I/O (реальный input() в терминале) не тестируется — только чистая
логика и функции с инъекцией ask/say, как велит executor.md для этого тикета.
"""
from __future__ import annotations

import pytest
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

import setup as setup_mod


# --------------------------------------------------------------------------
# .env — парсинг и запись
# --------------------------------------------------------------------------


def test_render_env_text_orders_required_keys_first():
    text = setup_mod.render_env_text(
        {
            "API_HASH": "abc",
            "API_ID": "123",
            "BOT_TOKEN": "t:token",
            "OWNER_ID": "999",
            "TZ": "Europe/Moscow",
        }
    )
    assert text.splitlines() == [
        "API_ID=123",
        "API_HASH=abc",
        "BOT_TOKEN=t:token",
        "OWNER_ID=999",
        "TZ=Europe/Moscow",
    ]


def test_render_env_text_keeps_intentionally_blank_value():
    # TZ пропущен пользователем (Enter) — ключ должен остаться в файле, но пустым.
    text = setup_mod.render_env_text(
        {"API_ID": "123", "API_HASH": "abc", "BOT_TOKEN": "t", "OWNER_ID": "1", "TZ": ""}
    )
    assert "TZ=" in text.splitlines()


def test_write_env_roundtrips_through_parse_env_text(tmp_path):
    path = tmp_path / ".env"
    values = {"API_ID": "42", "API_HASH": "h", "BOT_TOKEN": "tok", "OWNER_ID": "7", "TZ": ""}

    setup_mod.write_env(path, values)
    parsed = setup_mod.parse_env_text(path.read_text(encoding="utf-8"))

    assert parsed == values


def test_parse_env_text_ignores_comments_and_blank_lines():
    text = "# комментарий\n\nAPI_ID=1\nOWNER_ID=2\n"
    assert setup_mod.parse_env_text(text) == {"API_ID": "1", "OWNER_ID": "2"}


def test_validate_positive_int_rejects_non_numeric():
    with pytest.raises(ValueError):
        setup_mod.validate_positive_int("не число", "API_ID")


def test_validate_positive_int_accepts_digits():
    assert setup_mod.validate_positive_int(" 123 ", "API_ID") == 123


# --------------------------------------------------------------------------
# Сбор значений: объясняет раньше, чем спрашивает
# --------------------------------------------------------------------------


def test_ask_api_credentials_explains_before_asking():
    calls = []

    def say(msg):
        calls.append(("say", msg))

    answers = iter(["12345", "abcHASH"])

    def ask(prompt):
        calls.append(("ask", prompt))
        return next(answers)

    api_id, api_hash = setup_mod.ask_api_credentials(ask=ask, say=say)

    assert (api_id, api_hash) == (12345, "abcHASH")
    assert calls[0] == ("say", setup_mod.EXPLAIN_API_CREDS)
    assert "my.telegram.org" in calls[0][1]
    assert calls[1][0] == "ask"


def test_ask_api_credentials_reasks_on_invalid_api_id():
    calls = []
    say = lambda msg: calls.append(msg)
    answers = iter(["не число", "999", "hash-value"])
    ask = lambda prompt: next(answers)

    api_id, api_hash = setup_mod.ask_api_credentials(ask=ask, say=say)

    assert api_id == 999
    assert any("Ошибка" in msg for msg in calls)


def test_ask_bot_token_explains_botfather_before_asking():
    calls = []
    say = lambda msg: calls.append(("say", msg))
    ask = lambda prompt: calls.append(("ask", prompt)) or "123:TOKEN"

    token = setup_mod.ask_bot_token(ask=ask, say=say)

    assert token == "123:TOKEN"
    assert calls[0] == ("say", setup_mod.EXPLAIN_BOT_TOKEN)
    assert "BotFather" in calls[0][1]


def test_ask_tz_allows_intentional_skip():
    say = lambda msg: None
    ask = lambda prompt: ""

    assert setup_mod.ask_tz(ask=ask, say=say) == ""


# --------------------------------------------------------------------------
# Логин userbot — фейковый Telethon-клиент, без реального аккаунта
# --------------------------------------------------------------------------


class FakeSentCode:
    phone_code_hash = "hash-123"


class FakeUser:
    def __init__(self, first_name="Иван", user_id=1):
        self.first_name = first_name
        self.id = user_id


class FakeClient:
    """Имитирует интерфейс TelegramClient, который использует login_userbot."""

    def __init__(self, code_outcomes, password_outcomes=None):
        # code_outcomes: список — либо "ok", либо исключение, которое sign_in(code=...) бросит.
        self._code_outcomes = list(code_outcomes)
        self._password_outcomes = list(password_outcomes or [])
        self.connected = False
        self.authorized = False
        self.sign_in_calls = []

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        return FakeSentCode()

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        self.sign_in_calls.append({"phone": phone, "code": code, "password": password})
        if password is not None:
            outcome = self._password_outcomes.pop(0)
        else:
            outcome = self._code_outcomes.pop(0)
        if outcome == "ok":
            self.authorized = True
            return FakeUser()
        raise outcome

    async def get_me(self):
        return FakeUser()


@pytest.mark.asyncio
async def test_login_userbot_succeeds_on_first_correct_code():
    client = FakeClient(code_outcomes=["ok"])
    messages = []

    result = await setup_mod.login_userbot(
        client, "+79990000000", ask_code=lambda: "11111", ask_password=lambda: "", say=messages.append
    )

    assert result.first_name == "Иван"
    assert client.authorized is True
    assert messages == []


@pytest.mark.asyncio
async def test_login_userbot_reasks_after_invalid_code_then_succeeds():
    client = FakeClient(code_outcomes=[PhoneCodeInvalidError(request=None), "ok"])
    messages = []
    codes = iter(["00000", "11111"])

    result = await setup_mod.login_userbot(
        client, "+79990000000", ask_code=lambda: next(codes), ask_password=lambda: "", say=messages.append
    )

    assert client.authorized is True
    assert any("Неверный код" in m for m in messages)
    assert len(client.sign_in_calls) == 2


@pytest.mark.asyncio
async def test_login_userbot_raises_login_aborted_after_max_attempts():
    err = PhoneCodeInvalidError(request=None)
    client = FakeClient(code_outcomes=[err, err, err])
    messages = []

    with pytest.raises(setup_mod.LoginAborted):
        await setup_mod.login_userbot(
            client,
            "+79990000000",
            ask_code=lambda: "00000",
            ask_password=lambda: "",
            say=messages.append,
            max_attempts=3,
        )

    assert client.authorized is False
    assert len(client.sign_in_calls) == 3


@pytest.mark.asyncio
async def test_login_userbot_asks_for_2fa_password_when_required():
    client = FakeClient(
        code_outcomes=[SessionPasswordNeededError(request=None)],
        password_outcomes=["ok"],
    )
    messages = []

    result = await setup_mod.login_userbot(
        client,
        "+79990000000",
        ask_code=lambda: "11111",
        ask_password=lambda: "correct-pass",
        say=messages.append,
    )

    assert client.authorized is True
    assert result.first_name == "Иван"


@pytest.mark.asyncio
async def test_login_userbot_reasks_after_invalid_2fa_password():
    client = FakeClient(
        code_outcomes=[SessionPasswordNeededError(request=None)],
        password_outcomes=[PasswordHashInvalidError(request=None), "ok"],
    )
    messages = []
    passwords = iter(["wrong", "right"])

    result = await setup_mod.login_userbot(
        client,
        "+79990000000",
        ask_code=lambda: "11111",
        ask_password=lambda: next(passwords),
        say=messages.append,
    )

    assert client.authorized is True
    assert any("Неверный пароль" in m for m in messages)


@pytest.mark.asyncio
async def test_login_userbot_raises_login_aborted_after_max_invalid_passwords():
    err = PasswordHashInvalidError(request=None)
    client = FakeClient(
        code_outcomes=[SessionPasswordNeededError(request=None)],
        password_outcomes=[err, err, err],
    )
    messages = []

    with pytest.raises(setup_mod.LoginAborted):
        await setup_mod.login_userbot(
            client,
            "+79990000000",
            ask_code=lambda: "11111",
            ask_password=lambda: "wrong",
            say=messages.append,
            max_attempts=3,
        )


@pytest.mark.asyncio
async def test_login_both_sessions_logs_in_userbot_then_panel_with_same_phone():
    """R06/R07/R11: вторая, независимая сессия панели логинится тем же номером
    телефона, отдельным клиентом — переиспользуя `login_userbot` второй раз."""
    userbot_client = FakeClient(code_outcomes=["ok"])
    panel_client = FakeClient(code_outcomes=["ok"])
    messages = []
    codes = iter(["11111", "22222"])

    userbot_me, panel_me = await setup_mod.login_both_sessions(
        userbot_client,
        panel_client,
        "+79990000000",
        ask_code=lambda: next(codes),
        ask_password=lambda: "",
        say=messages.append,
    )

    assert userbot_client.authorized is True
    assert panel_client.authorized is True
    assert userbot_client.sign_in_calls == [
        {"phone": "+79990000000", "code": "11111", "password": None}
    ]
    assert panel_client.sign_in_calls == [
        {"phone": "+79990000000", "code": "22222", "password": None}
    ]
    assert userbot_me.first_name == "Иван"
    assert panel_me.first_name == "Иван"
    assert any("панели" in m for m in messages)


@pytest.mark.asyncio
async def test_login_both_sessions_skips_panel_login_when_already_authorized():
    userbot_client = FakeClient(code_outcomes=["ok"])
    panel_client = FakeClient(code_outcomes=[])
    panel_client.authorized = True

    await setup_mod.login_both_sessions(
        userbot_client,
        panel_client,
        "+79990000000",
        ask_code=lambda: "11111",
        ask_password=lambda: "",
        say=lambda m: None,
    )

    assert panel_client.sign_in_calls == []


@pytest.mark.asyncio
async def test_login_userbot_skips_login_when_already_authorized():
    client = FakeClient(code_outcomes=[])
    client.authorized = True

    result = await setup_mod.login_userbot(
        client,
        "+79990000000",
        ask_code=lambda: (_ for _ in ()).throw(AssertionError("код не должен запрашиваться")),
        ask_password=lambda: "",
        say=lambda m: None,
    )

    assert result.first_name == "Иван"
    assert client.sign_in_calls == []


# --------------------------------------------------------------------------
# Финальная проверка живости — три факта
# --------------------------------------------------------------------------


class FakeMe:
    def __init__(self, first_name=None, username=None, user_id=None):
        self.first_name = first_name
        self.username = username
        self.id = user_id


class FakeLivenessClient:
    def __init__(self, me):
        self._me = me

    async def get_me(self):
        return self._me


@pytest.mark.asyncio
async def test_verify_liveness_reports_userbot_panel_bot_and_owner():
    userbot_client = FakeLivenessClient(FakeMe(first_name="Иван", user_id=111))
    panel_client = FakeLivenessClient(FakeMe(first_name="Иван", user_id=111))
    bot_client = FakeLivenessClient(FakeMe(username="my_lead_bot"))

    result = await setup_mod.verify_liveness(userbot_client, panel_client, bot_client, owner_id=555)

    assert result.userbot_name == "Иван"
    assert result.panel_name == "Иван"
    assert result.bot_username == "my_lead_bot"
    assert result.owner_id == 555


def test_format_liveness_contains_all_four_facts():
    result = setup_mod.LivenessResult(
        userbot_name="Иван", panel_name="Иван", bot_username="my_lead_bot", owner_id=555
    )

    text = setup_mod.format_liveness(result)

    assert text.count("Иван") == 2
    assert "my_lead_bot" in text
    assert "555" in text
