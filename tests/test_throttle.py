"""Rate limiter must actually cap throughput, and caching must round-trip."""

import time

from src.ingestion._throttle import RateLimiter, _cache_path


def test_rate_limiter_blocks_over_budget(monkeypatch):
    # 2 calls/min: the 3rd acquire would need to wait ~window seconds. We shrink the
    # window via monkeypatch to keep the test fast while proving the logic.
    rl = RateLimiter(rpm=2, name="t")
    rl._window = 0.3
    start = time.monotonic()
    rl.acquire()
    rl.acquire()
    rl.acquire()  # this one must wait for the window to roll
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25


def test_cache_path_is_stable_and_unique():
    a = _cache_path("https://example.com?x=1")
    b = _cache_path("https://example.com?x=1")
    c = _cache_path("https://example.com?x=2")
    assert a == b
    assert a != c
    assert a.suffix == ".json"
