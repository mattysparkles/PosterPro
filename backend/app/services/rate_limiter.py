from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RateLimitProfile:
    marketplace: str
    daily_limit: int
    max_per_second: float


class RateLimiter:
    """Centralized async/sync marketplace rate limiter with daily + per-second gates."""

    def __init__(self) -> None:
        self._profiles: dict[str, RateLimitProfile] = {
            "ebay": RateLimitProfile(marketplace="ebay", daily_limit=4000, max_per_second=2.0),
            "etsy": RateLimitProfile(marketplace="etsy", daily_limit=10000, max_per_second=3.0),
            "poshmark": RateLimitProfile(marketplace="poshmark", daily_limit=8000, max_per_second=2.0),
            "mercari": RateLimitProfile(marketplace="mercari", daily_limit=8000, max_per_second=2.0),
            "depop": RateLimitProfile(marketplace="depop", daily_limit=6000, max_per_second=1.5),
            "whatnot": RateLimitProfile(marketplace="whatnot", daily_limit=6000, max_per_second=1.5),
            "facebook": RateLimitProfile(marketplace="facebook", daily_limit=9000, max_per_second=2.0),
            "vinted": RateLimitProfile(marketplace="vinted", daily_limit=7000, max_per_second=2.0),
        }
        self._daily_counters: dict[tuple[str, str], int] = defaultdict(int)
        self._last_call_at: dict[str, float] = defaultdict(float)

    def _profile(self, marketplace: str) -> RateLimitProfile:
        key = str(marketplace or "").lower().strip()
        return self._profiles.get(key, RateLimitProfile(marketplace=key or "default", daily_limit=5000, max_per_second=2.0))

    def _today_key(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _reserve(self, marketplace: str, cost: int = 1) -> float:
        profile = self._profile(marketplace)
        cost = max(1, int(cost or 1))
        day_key = self._today_key()
        counter_key = (profile.marketplace, day_key)
        used = self._daily_counters[counter_key]
        if used + cost > profile.daily_limit:
            raise RuntimeError(
                f"Rate limit reached for {profile.marketplace}: {used}/{profile.daily_limit} requests used today"
            )

        min_interval = 1.0 / max(0.1, profile.max_per_second)
        now = time.monotonic()
        next_allowed = self._last_call_at[profile.marketplace] + min_interval
        wait_seconds = max(0.0, next_allowed - now)

        self._daily_counters[counter_key] = used + cost
        self._last_call_at[profile.marketplace] = now + wait_seconds
        return wait_seconds

    async def acquire_async(self, marketplace: str, *, cost: int = 1) -> None:
        wait_seconds = self._reserve(marketplace, cost=cost)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

    def acquire(self, marketplace: str, *, cost: int = 1) -> None:
        wait_seconds = self._reserve(marketplace, cost=cost)
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def suggested_chunk_size(self, marketplace: str, total_items: int, max_parallel_tasks: int) -> int:
        profile = self._profile(marketplace)
        if total_items <= 0:
            return 1
        max_parallel_tasks = max(1, max_parallel_tasks)
        quota_headroom = max(1, math.floor(profile.daily_limit * 0.25))
        target_tasks = min(max_parallel_tasks, quota_headroom)
        return max(1, math.ceil(total_items / target_tasks))


rate_limiter = RateLimiter()
