"""
Cache-backed rate limiting.

Deliberately small. Every OTP send costs real money (roughly R0.25 per SMS),
so an unthrottled send endpoint is not just an abuse vector, it is a way for a
stranger to spend your budget. Throttle by number AND by IP: throttling by
number alone lets one attacker walk through a list of numbers, and throttling
by IP alone lets one number be hammered from many sources.
"""
import time

from django.core.cache import cache


class RateLimited(Exception):
    def __init__(self, retry_after: int, message: str = ""):
        self.retry_after = max(1, int(retry_after))
        self.message = message or (
            f"Too many attempts. Please try again in {self.retry_after} seconds."
        )
        super().__init__(self.message)


def hit(key: str, limit: int, window_seconds: int, message: str = "") -> int:
    """
    Register one use of `key`. Raises RateLimited if the limit is exceeded.
    Returns the current count.

    Fixed-window counter. Not perfectly fair at window boundaries, which is
    fine for this purpose and much cheaper than a sliding log.
    """
    cache_key = f"rl:{key}"
    added = cache.add(cache_key, 1, timeout=window_seconds)
    if added:
        return 1
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # Key expired between add() and incr()
        cache.set(cache_key, 1, timeout=window_seconds)
        return 1
    if count > limit:
        raise RateLimited(window_seconds, message)
    return count


def cooldown(key: str, seconds: int, message: str = "") -> None:
    """Enforce a minimum gap between two actions on the same key."""
    cache_key = f"cd:{key}"
    last = cache.get(cache_key)
    now = time.time()
    if last is not None:
        remaining = int(seconds - (now - last))
        if remaining > 0:
            raise RateLimited(remaining, message)
    cache.set(cache_key, now, timeout=seconds)


def clear(key: str) -> None:
    cache.delete(f"rl:{key}")
    cache.delete(f"cd:{key}")


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")
