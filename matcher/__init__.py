"""Матчинг ключевых слов и минус-слов — чистая логика, без сети и без Telegram."""
from .ruleset import (
    InvalidKeywordError,
    MatchResult,
    Ruleset,
    compile,
)

__all__ = ["compile", "Ruleset", "MatchResult", "InvalidKeywordError"]
