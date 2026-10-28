"""Per-minute rate limits for any service.

With REDIS_URL set the count is shared by every worker and container (a
hard limit); without Redis - or while it can't be reached - each process
counts on its own, so a limiter outage never turns requests away. (The
training links' limiter in training_policy.py predates this and works the
same way.)
"""

from __future__ import annotations

import functools
import logging
import time
from collections import defaultdict, deque

from app.config import get_settings

_local: dict[str, deque] = defaultdict(deque)


@functools.lru_cache
def _redis_client(url: str):
    import redis

    return redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)


def _shared(key: str) -> int | None:
    url = get_settings().redis_url
    if not url:
        return None
    try:
        name = f"brt360:ratelimit:{key}:{int(time.time() // 60)}"
        pipe = _redis_client(url).pipeline()
        pipe.incr(name)
        pipe.expire(name, 120)
        hits, _ = pipe.execute()
        return int(hits)
    except Exception:  # noqa: BLE001 - a limiter outage must not take a service down
        logging.getLogger(__name__).warning("Rate limiter: Redis unavailable, using this process's count", exc_info=True)
        return None


def allow(key: str, per_minute: int) -> bool:
    """Counts one hit on `key`; False once it has had more than per_minute
    in the current minute. per_minute <= 0 means no limit."""
    if per_minute <= 0:
        return True
    shared = _shared(key)
    if shared is not None:
        return shared <= per_minute
    now = time.monotonic()
    window = _local[key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= per_minute:
        return False
    window.append(now)
    return True
