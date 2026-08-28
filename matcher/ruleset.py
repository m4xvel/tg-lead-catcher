"""Компиляция ключевых слов и минус-слов в правило совпадения (R17-R21).

Модуль не знает ни про Telegram, ни про БД — принимает на вход пары
``(kind, pattern)`` (так их отдаёт store.keywords) и текст, отдаёт результат
матчинга. Разбор синтаксиса «сырого» пользовательского ввода (кавычки, префикс
``re:``) сюда не входит — на вход уже приходит классифицированная пара.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_YO_TO_E = str.maketrans({"ё": "е", "Ё": "Е"})


class InvalidKeywordError(ValueError):
    """Ключ вида regex не компилируется — понятная ошибка для UI (R19.1)."""


def _normalize(text: str) -> str:
    return text.translate(_YO_TO_E).lower()


@dataclass(frozen=True)
class _CompiledKeyword:
    pattern: str  # исходный (нормализованный) паттерн — для hit_keywords/blocked_by
    regex: re.Pattern[str]


def _compile_one(kind: str, pattern: str) -> _CompiledKeyword:
    if kind == "word":
        normalized_pattern = _normalize(pattern)
        regex = re.compile(r"\b" + re.escape(normalized_pattern) + r"\w*")
    elif kind == "phrase":
        normalized_pattern = _normalize(pattern)
        regex = re.compile(r"\b" + re.escape(normalized_pattern) + r"\b")
    elif kind == "regex":
        # ВАЖНО: regex-паттерн НЕ пропускается через _normalize()/.lower() —
        # это ломало бы regex-синтаксис (\D -> \d, \S -> \s, \W -> \w,
        # [A-Z] -> [a-z] и т.п.), инвертируя смысл ключа. re.IGNORECASE
        # достаточно для регистронезависимости; автоматической ё/е-подмены
        # для сырого regex-синтаксиса сознательно нет — если нужна
        # ё/е-эквивалентность, пользователь пишет её сам (например, [ёе]).
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise InvalidKeywordError(
                f"Некорректное регулярное выражение «{pattern}»: {exc}"
            ) from exc
    else:
        raise InvalidKeywordError(f"Неизвестный тип ключа: «{kind}»")
    return _CompiledKeyword(pattern=pattern, regex=regex)


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    hit_keywords: list[str] = field(default_factory=list)
    blocked_by: str | None = None


@dataclass(frozen=True)
class Ruleset:
    _keywords: tuple[_CompiledKeyword, ...]
    _stopwords: tuple[_CompiledKeyword, ...]

    def match(self, text: str) -> MatchResult:
        haystack = _normalize(text)

        hit_keywords = [kw.pattern for kw in self._keywords if kw.regex.search(haystack)]

        blocked_by = next(
            (stop.pattern for stop in self._stopwords if stop.regex.search(haystack)),
            None,
        )

        matched = bool(hit_keywords) and blocked_by is None
        return MatchResult(matched=matched, hit_keywords=hit_keywords, blocked_by=blocked_by)


def compile(keywords, stopwords) -> Ruleset:
    """Компилирует ключи и минус-слова в Ruleset.

    ``keywords``/``stopwords`` — итерируемые пары ``(kind, pattern)``, где
    ``kind`` один из ``word``/``phrase``/``regex``. Бросает
    :class:`InvalidKeywordError` с понятным текстом, если какой-то ``regex``
    не компилируется — ключ не должен попасть в список (R19.1).
    """
    compiled_keywords = tuple(_compile_one(kind, pattern) for kind, pattern in keywords)
    compiled_stopwords = tuple(_compile_one(kind, pattern) for kind, pattern in stopwords)
    return Ruleset(_keywords=compiled_keywords, _stopwords=compiled_stopwords)
