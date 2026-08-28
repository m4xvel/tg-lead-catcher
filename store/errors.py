"""Ошибки store — прячут детали SQL от вызывающего кода."""


class DuplicateHitError(Exception):
    """Срабатывание с таким (source_chat_id, message_id) уже записано (R32)."""


class DuplicateChatError(Exception):
    """Чат с таким chat_id уже есть в списке (sources/receivers)."""
