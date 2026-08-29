"""Мастер первичной настройки tg-lead-catcher.

Отдельная точка входа: `python configure.py`. Не импортируется рантаймом
(`userbot`/`panel`) и не запускается как часть Docker-образа — только руками,
один раз при первом запуске (и повторно после падения сессии, R64).

Шаги: объясняет, где взять секреты → собирает их → пишет `.env` → логинит
userbot в Telethon (код и 2FA — только здесь, в терминале, никогда через
бота) → печатает финальную проверку живости.

Тестируемая часть (парсинг/запись `.env`, разбор ошибок логина) вынесена в
чистые функции и функции с инъекцией `ask`/`say` — они гоняются в
`tests/test_configure.py` с фейковым Telethon-клиентом, без реального ввода и
без сети. Интерактивный `main()` — тонкая обвязка поверх них, вручную не
тестируется.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from telethon import TelegramClient
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

REPO_ROOT = Path(__file__).parent
ENV_PATH = REPO_ROOT / ".env"
SESSION_PATH = REPO_ROOT / "data" / "userbot"
# panel — отдельный процесс (docker-compose.yml), не должен делить сессию с
# userbot (гонка/повреждение SQLite при одновременном доступе двух процессов —
# см. panel/main.py). Тот же аккаунт, тот же номер телефона, отдельный файл —
# Telegram поддерживает несколько параллельных сессий на одном аккаунте.
PANEL_SESSION_PATH = REPO_ROOT / "data" / "panel"

# Порядок, в котором ключи попадают в .env — остальные (если появятся) идут следом по алфавиту.
REQUIRED_KEYS = ["API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID", "TZ"]
DEFAULT_TZ = "Europe/Moscow"
MAX_LOGIN_ATTEMPTS = 3

EXPLAIN_API_CREDS = (
    "API_ID и API_HASH выдаёт Telegram на https://my.telegram.org — войдите под своим "
    "номером телефона, откройте 'API development tools' и создайте приложение (любое "
    "название подойдёт). Оттуда скопируйте api_id и api_hash."
)
EXPLAIN_BOT_TOKEN = (
    "Токен бота выдаёт @BotFather в Telegram: напишите ему /newbot, придумайте имя и "
    "username — в ответ придёт токен вида 123456789:AA... Его и нужно ввести."
)
EXPLAIN_OWNER_ID = (
    "Нужен ваш числовой Telegram ID — только он получит доступ к панели. Узнать его "
    "можно у бота @userinfobot: перешлите ему любое сообщение или просто напишите /start."
)
EXPLAIN_PHONE = (
    "Нужен номер телефона аккаунта, от имени которого userbot будет читать чаты — "
    "в международном формате, например +79991234567."
)


class LoginAborted(Exception):
    """Попытки ввода кода подтверждения или 2FA-пароля исчерпаны."""


# --------------------------------------------------------------------------
# .env — парсинг и запись (чистые функции)
# --------------------------------------------------------------------------


def parse_env_text(text: str) -> dict[str, str]:
    """Разбирает содержимое .env в словарь key -> value.

    Пустые строки и строки-комментарии (`#...`) игнорируются.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def render_env_text(values: dict[str, str]) -> str:
    """Формирует текст .env: сначала REQUIRED_KEYS по порядку, затем остальные по алфавиту.

    Значения, оставленные пользователем пустыми намеренно, попадают в файл как
    `KEY=` — сам ключ не пропадает, чтобы `.env` оставался полным по составу имён.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for key in REQUIRED_KEYS:
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
    for key in sorted(k for k in values if k not in seen):
        lines.append(f"{key}={values[key]}")
    return "\n".join(lines) + "\n"


def write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(render_env_text(values), encoding="utf-8")


def validate_positive_int(raw: str, field_name: str) -> int:
    """API_ID и OWNER_ID — положительные целые числа без символов вроде @ или пробелов."""
    raw = raw.strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{field_name} должен быть положительным числом, без лишних символов")
    return int(raw)


def validate_non_empty(raw: str, field_name: str) -> str:
    raw = raw.strip()
    if not raw:
        raise ValueError(f"{field_name} не может быть пустым")
    return raw


# --------------------------------------------------------------------------
# Сбор значений — say/ask инъецируются, чтобы гонять логику без реального терминала
# --------------------------------------------------------------------------


def ask_validated(
    prompt: str,
    validate: Callable[[str], object],
    ask: Callable[[str], str],
    say: Callable[[str], None],
) -> object:
    while True:
        raw = ask(prompt)
        try:
            return validate(raw)
        except ValueError as exc:
            say(f"Ошибка: {exc}")


def ask_api_credentials(
    ask: Callable[[str], str] = input, say: Callable[[str], None] = print
) -> tuple[int, str]:
    say(EXPLAIN_API_CREDS)
    api_id = ask_validated("API_ID: ", lambda r: validate_positive_int(r, "API_ID"), ask, say)
    api_hash = ask_validated(
        "API_HASH: ", lambda r: validate_non_empty(r, "API_HASH"), ask, say
    )
    return api_id, api_hash


def ask_bot_token(ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> str:
    say(EXPLAIN_BOT_TOKEN)
    return ask_validated(
        "Токен бота (от @BotFather): ", lambda r: validate_non_empty(r, "Токен бота"), ask, say
    )


def ask_owner_id(ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> int:
    say(EXPLAIN_OWNER_ID)
    return ask_validated(
        "Ваш Telegram ID: ", lambda r: validate_positive_int(r, "Telegram ID"), ask, say
    )


def ask_phone(ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> str:
    say(EXPLAIN_PHONE)
    return ask_validated("Номер телефона: ", lambda r: validate_non_empty(r, "Номер телефона"), ask, say)


def ask_tz(ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> str:
    """Часовой пояс — единственное поле, которое можно пропустить намеренно (Enter)."""
    say(
        f"Часовой пояс для отметок времени в карточках (например {DEFAULT_TZ}). "
        "Можно пропустить — Enter."
    )
    raw = ask(f"Часовой пояс [{DEFAULT_TZ}, Enter — пропустить]: ").strip()
    return raw


def collect_env_values(
    ask: Callable[[str], str] = input, say: Callable[[str], None] = print
) -> tuple[dict[str, str], str]:
    """Собирает значения для .env. Возвращает (values, phone) — телефон в .env не пишется."""
    api_id, api_hash = ask_api_credentials(ask, say)
    bot_token = ask_bot_token(ask, say)
    owner_id = ask_owner_id(ask, say)
    phone = ask_phone(ask, say)
    tz = ask_tz(ask, say)
    values = {
        "API_ID": str(api_id),
        "API_HASH": api_hash,
        "BOT_TOKEN": bot_token,
        "OWNER_ID": str(owner_id),
        "TZ": tz,
    }
    return values, phone


# --------------------------------------------------------------------------
# Логин userbot — код и 2FA только через ask_code/ask_password (терминал мастера)
# --------------------------------------------------------------------------


async def login_userbot(
    client,
    phone: str,
    ask_code: Callable[[], str],
    ask_password: Callable[[], str],
    say: Callable[[str], None],
    max_attempts: int = MAX_LOGIN_ATTEMPTS,
):
    """Логинит userbot-клиент, спрашивая код подтверждения и (если включена) 2FA.

    `client` — любой объект с интерфейсом Telethon `TelegramClient` (connect,
    is_user_authorized, send_code_request, sign_in, get_me) — в тестах это
    фейковый клиент за тем же интерфейсом, без реального аккаунта.
    Неверный код/пароль не роняет функцию — печатает понятную ошибку через `say`
    и запрашивает снова, до `max_attempts`. После исчерпания попыток — LoginAborted.
    Код и пароль запрашиваются только через переданные колбэки (в реальном запуске —
    input() в терминале мастера), Bot API здесь не участвует.
    """
    await client.connect()
    if await client.is_user_authorized():
        return await client.get_me()

    sent = await client.send_code_request(phone)

    signed_in = False
    needs_password = False
    for attempt in range(1, max_attempts + 1):
        code = ask_code()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            signed_in = True
            break
        except SessionPasswordNeededError:
            needs_password = True
            break
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            say("Неверный код подтверждения. Проверьте цифры в SMS/Telegram и попробуйте снова.")

    if not signed_in and not needs_password:
        raise LoginAborted(
            "Код подтверждения введён неверно несколько раз подряд — запустите configure.py заново"
        )

    if needs_password:
        password_ok = False
        for attempt in range(1, max_attempts + 1):
            password = ask_password()
            try:
                await client.sign_in(password=password)
                password_ok = True
                break
            except PasswordHashInvalidError:
                say("Неверный пароль двухфакторной аутентификации. Попробуйте снова.")
        if not password_ok:
            raise LoginAborted(
                "2FA-пароль введён неверно несколько раз подряд — запустите configure.py заново"
            )

    return await client.get_me()


async def login_both_sessions(
    userbot_client,
    panel_client,
    phone: str,
    ask_code: Callable[[], str],
    ask_password: Callable[[], str],
    say: Callable[[str], None],
    max_attempts: int = MAX_LOGIN_ATTEMPTS,
):
    """Логинит ОБЕ сессии — userbot и panel — тем же номером телефона, одним аккаунтом.

    Panel — отдельный процесс (см. `panel/main.py`), которому нельзя делить файл
    сессии с userbot (гонка/повреждение SQLite при одновременном доступе двух
    процессов). Решение — не общий файл, а вторая независимая авторизация того
    же аккаунта: Telegram нормально держит несколько параллельных сессий (как
    Desktop и Web одновременно). Переиспользует `login_userbot` дважды — код
    подтверждения (и 2FA, если включена) для каждой сессии по-прежнему
    спрашивается только в терминале мастера (R58), через переданные `ask_code`/
    `ask_password`.

    Возвращает `(userbot_me, panel_me)`.
    """
    userbot_me = await login_userbot(userbot_client, phone, ask_code, ask_password, say, max_attempts)
    say(
        "Основная сессия userbot авторизована. Теперь — вторая, независимая сессия "
        "для панели (тот же номер телефона, отдельный файл). Придёт ещё один код "
        "подтверждения."
    )
    panel_me = await login_userbot(panel_client, phone, ask_code, ask_password, say, max_attempts)
    return userbot_me, panel_me


# --------------------------------------------------------------------------
# Финальная проверка живости
# --------------------------------------------------------------------------


@dataclass
class LivenessResult:
    userbot_name: str
    panel_name: str
    bot_username: str
    owner_id: int


async def verify_liveness(userbot_client, panel_client, bot_client, owner_id: int) -> LivenessResult:
    """userbot жив (get_me), panel-сессия жива (get_me), бот отвечает (get_me),
    доступ владельца — введённый ID."""
    me = await userbot_client.get_me()
    panel_me = await panel_client.get_me()
    bot_me = await bot_client.get_me()
    userbot_name = getattr(me, "first_name", None) or str(me.id)
    panel_name = getattr(panel_me, "first_name", None) or str(panel_me.id)
    return LivenessResult(
        userbot_name=userbot_name,
        panel_name=panel_name,
        bot_username=bot_me.username,
        owner_id=owner_id,
    )


def format_liveness(result: LivenessResult) -> str:
    return (
        f"userbot подключен как {result.userbot_name} "
        f"(panel-сессия — как {result.panel_name}), "
        f"бот @{result.bot_username} жив, "
        f"доступ у ID {result.owner_id}"
    )


# --------------------------------------------------------------------------
# Интерактивная точка входа
# --------------------------------------------------------------------------


async def _run_interactive() -> None:
    print("=== Мастер настройки tg-lead-catcher ===")
    print(
        "Секреты нигде не сохраняются, кроме .env на этой машине — не пересылайте их "
        "никому, включая ботов."
    )

    values, phone = collect_env_values()
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_env(ENV_PATH, values)
    print(f"Записано в {ENV_PATH}")

    userbot_client = TelegramClient(str(SESSION_PATH), int(values["API_ID"]), values["API_HASH"])
    panel_client = TelegramClient(str(PANEL_SESSION_PATH), int(values["API_ID"]), values["API_HASH"])
    try:
        print(
            "Сейчас придёт код подтверждения в Telegram/SMS. Введите его здесь — "
            "никогда не отправляйте его в переписке с ботом. Это повторится дважды: "
            "сначала для основной сессии userbot, затем для отдельной сессии панели "
            "(тот же аккаунт, две независимые сессии — как Desktop и Web)."
        )
        await login_both_sessions(
            userbot_client,
            panel_client,
            phone,
            ask_code=lambda: input("Код подтверждения: "),
            ask_password=lambda: input("Пароль 2FA: "),
            say=print,
        )

        bot_client = TelegramClient(
            str(REPO_ROOT / "data" / "bot"), int(values["API_ID"]), values["API_HASH"]
        )
        await bot_client.start(bot_token=values["BOT_TOKEN"])
        try:
            result = await verify_liveness(
                userbot_client, panel_client, bot_client, int(values["OWNER_ID"])
            )
            print(format_liveness(result))
        finally:
            await bot_client.disconnect()
    except LoginAborted as exc:
        print(f"Настройка не завершена: {exc}")
        raise SystemExit(1)
    finally:
        await userbot_client.disconnect()
        await panel_client.disconnect()


def main() -> None:
    asyncio.run(_run_interactive())


if __name__ == "__main__":
    main()
