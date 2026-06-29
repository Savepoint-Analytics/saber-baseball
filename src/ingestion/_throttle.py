"""Shared rate-limiting, on-disk caching, and backoff for all ingestion.

Compliance is enforced *here* so every source puller is a good upstream citizen by
construction (see ``CLAUDE.md`` §5, ``public-data-sources.md``):

* ``RateLimiter`` — token-bucket capped at a per-source requests/minute budget.
* ``cached_get`` — TTL'd on-disk HTTP cache so we never re-hit a source for data we
  already pulled (this also makes runs reproducible and fast).
* ``with_backoff`` — exponential backoff/retry on transient failures via ``tenacity``.
* ``enable_pybaseball_cache`` — flips on pybaseball's own cache for the same reason.

These tools are deliberately conservative. If a source publishes a hard cap (e.g.
Baseball Reference at 20 req/min), we sit safely below it.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import requests
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings


class RateLimiter:
    """Thread-safe sliding-window limiter: at most ``rpm`` calls per 60s.

    ``acquire()`` blocks until making a call would respect the budget. One limiter
    per upstream host so independent sources don't starve each other.
    """

    def __init__(self, rpm: int, name: str = "source") -> None:
        if rpm <= 0:
            raise ValueError("rpm must be positive")
        self.rpm = rpm
        self.name = name
        self._window = 60.0
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            # Drop timestamps older than the window.
            while self._calls and now - self._calls[0] >= self._window:
                self._calls.popleft()
            if len(self._calls) >= self.rpm:
                sleep_for = self._window - (now - self._calls[0])
                if sleep_for > 0:
                    logger.debug(
                        "[{}] rate limit hit ({}/min); sleeping {:.2f}s",
                        self.name,
                        self.rpm,
                        sleep_for,
                    )
                    time.sleep(sleep_for)
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self._window:
                    self._calls.popleft()
            self._calls.append(time.monotonic())


# One limiter per upstream, sized from settings (which read .env).
LIMITERS: dict[str, RateLimiter] = {
    "bbref": RateLimiter(settings.bbref_rpm, "bbref"),
    "savant": RateLimiter(settings.savant_rpm, "savant"),
    "statsapi": RateLimiter(settings.statsapi_rpm, "statsapi"),
}


def get_limiter(source: str) -> RateLimiter:
    """Return the limiter for a source, defaulting to a conservative 30/min."""
    return LIMITERS.setdefault(source, RateLimiter(30, source))


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings.cache_dir / f"{digest}.json"


def _cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400.0
    return age_days < settings.http_cache_ttl_days


@retry(
    retry=retry_if_exception_type((requests.RequestException,)),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _do_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    headers = {"User-Agent": settings.user_agent, **kwargs.pop("headers", {})}
    resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


def cached_get(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any] | list[Any]:
    """Rate-limited, cached, retried GET that returns parsed JSON.

    Use for JSON HTTP APIs (e.g. statsapi.mlb.com). For library-mediated pulls
    (pybaseball), use :func:`enable_pybaseball_cache` plus :func:`with_backoff`.
    """
    cache_key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
    path = _cache_path(cache_key)
    if not force and _cache_fresh(path):
        logger.debug("[{}] cache hit: {}", source, url)
        return json.loads(path.read_text())

    get_limiter(source).acquire()
    logger.info("[{}] GET {}", source, url)
    resp = _do_request("GET", url, params=params)
    data = resp.json()
    path.write_text(json.dumps(data))
    return data


def with_backoff(func: Any) -> Any:
    """Decorator: exponential backoff/retry for library-mediated pulls."""
    return retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )(func)


def enable_pybaseball_cache() -> None:
    """Enable pybaseball's built-in cache to avoid re-hitting upstream sources."""
    try:
        import pybaseball

        pybaseball.cache.enable()
        logger.debug("pybaseball cache enabled")
    except Exception as exc:  # pragma: no cover - optional dep / version drift
        logger.warning("could not enable pybaseball cache: {}", exc)
