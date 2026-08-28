"""RateLimiter — окно 60с (R62): всплеск не превышает лимит в минуту."""
from __future__ import annotations

from worker.ratelimit import RateLimiter


def test_burst_of_50_at_limit_20_allows_only_20_within_window():
    limiter = RateLimiter(20)

    allowed = [limiter.allow(now=1000.0 + i * 0.01) for i in range(50)]

    assert sum(allowed) == 20
    assert allowed[:20] == [True] * 20
    assert allowed[20:] == [False] * 30


def test_slot_frees_up_after_window_passes():
    limiter = RateLimiter(20)
    for i in range(20):
        assert limiter.allow(now=1000.0 + i * 0.01) is True

    # тот же момент — лимит исчерпан
    assert limiter.allow(now=1000.5) is False

    # прошло больше 60с — самый старый слот вышел из окна, есть место
    assert limiter.allow(now=1061.0) is True
