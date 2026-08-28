"""Тесты шва matcher.Ruleset.match — весь матчинг R17-R21, без сети и без БД."""
import pytest

import matcher


def test_word_catches_wordforms_by_boundary():
    ruleset = matcher.compile([("word", "ремонт")], [])

    assert ruleset.match("ремонта").matched is True
    assert ruleset.match("ремонтом").matched is True
    assert ruleset.match("ремонтник").matched is True
    assert ruleset.match("автора").matched is False


def test_phrase_catches_only_exact_sequence():
    ruleset = matcher.compile([("phrase", "ищу подрядчика")], [])

    assert ruleset.match("срочно ищу подрядчика на ремонт").matched is True
    # слова есть оба, но не подряд — фраза не должна сработать
    assert ruleset.match("ищу хорошего подрядчика").matched is False


def test_regex_matches_pattern():
    ruleset = matcher.compile([("regex", r"\d{3}-\d{2}")], [])

    assert ruleset.match("звоните 123-45 после обеда").matched is True
    assert ruleset.match("звоните позже").matched is False


def test_invalid_regex_rejected_with_message():
    with pytest.raises(matcher.InvalidKeywordError):
        matcher.compile([("regex", "(")], [])


def test_case_and_yo_e_are_equivalent():
    ruleset_upper = matcher.compile([("word", "ремонт")], [])
    assert ruleset_upper.match("Нужен РЕМОНТ квартиры").matched is True

    ruleset_yo = matcher.compile([("word", "ещё")], [])
    assert ruleset_yo.match("нужно еще кое-что").matched is True

    ruleset_e = matcher.compile([("word", "еще")], [])
    assert ruleset_e.match("нужно ещё кое-что").matched is True


def test_regex_is_case_insensitive_but_not_yo_e_normalized():
    # regex НЕ проходит через _normalize()/.lower() (иначе \D/\S/\W и классы
    # символов теряют смысл — см. test_regex_char_classes_not_inverted), но
    # остаётся регистронезависимым за счёт re.IGNORECASE.
    ruleset = matcher.compile([("regex", "ремонт")], [])
    assert ruleset.match("Нужен РЕМОНТ квартиры").matched is True

    # ё/е-эквивалентность для regex больше не подставляется автоматически —
    # если она нужна, пользователь пишет её сам в паттерне (например, [ёе]).
    ruleset_yo_key = matcher.compile([("regex", "ремонтёр")], [])
    assert ruleset_yo_key.match("ищу ремонтера").matched is False

    ruleset_explicit = matcher.compile([("regex", "ремонт[ёе]р")], [])
    assert ruleset_explicit.match("ищу ремонтера").matched is True
    assert ruleset_explicit.match("ищу ремонтёра").matched is True


def test_regex_char_classes_not_inverted():
    # Регрессия: раньше _normalize() вызывал .lower() на самом паттерне,
    # превращая \D -> \d, \S -> \s, \W -> \w, [A-Z] -> [a-z] — то есть
    # regex-ключ после компиляции матчил ПРОТИВОПОЛОЖНОЕ тому, что написал
    # пользователь. Паттерн должен компилироваться как есть.
    ruleset = matcher.compile([("regex", r"\D{3}")], [])
    assert ruleset.match("abc").matched is True
    assert ruleset.match("123").matched is False

    ruleset_upper = matcher.compile([("regex", r"[A-Z]{2}")], [])
    # re.IGNORECASE делает класс регистронезависимым, но класс остаётся
    # «буквы», а не превращается в цифры/что-то ещё.
    assert ruleset_upper.match("ab").matched is True
    assert ruleset_upper.match("12").matched is False


def test_stopword_blocks_match_and_is_named():
    ruleset = matcher.compile([("word", "ремонт")], [("word", "вакансия")])

    result = ruleset.match("нужен ремонт, есть вакансия")

    assert result.matched is False
    assert result.blocked_by is not None
    assert "вакансия" in result.blocked_by
    assert "ремонт" in result.hit_keywords
