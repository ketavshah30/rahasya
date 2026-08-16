import asyncio
import time
import functools
from typing import Any, Dict, Optional
from loguru import logger

try:
    import redis.asyncio as redis
except ImportError:
    redis = None


class TokenBucketRateLimiter:
    """In-memory Token Bucket rate limiter."""
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                logger.debug(f"Rate limiting: waiting {wait_time:.3f}s")
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_update = time.monotonic()
            else:
                self.tokens -= 1.0


class RedisRateLimiter:
    """Redis-backed distributed rate limiter using sliding window log."""
    def __init__(self, redis_client, key: str, rate: float, burst: int):
        self.redis = redis_client
        self.key = f"ratelimit:{key}"
        self.rate = rate
        self.burst = burst
        self.window = burst / rate

    async def acquire(self) -> None:
        while True:
            now = time.time()
            # Remove old entries
            await self.redis.zremrangebyscore(self.key, 0, now - self.window)
            count = await self.redis.zcard(self.key)
            
            if count < self.burst:
                await self.redis.zadd(self.key, {str(now): now})
                await self.redis.expire(self.key, int(self.window) + 1)
                break
            
            # Wait a bit and retry
            await asyncio.sleep(0.1)


class RateLimiterRegistry:
    """Global registry of rate limiters."""
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_client = None
        if redis is not None and redis_url:
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            
        self._limiters: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def get_limiter(self, key: str, rate: float, burst: int):
        async with self._lock:
            if key not in self._limiters:
                if self.redis_client:
                    self._limiters[key] = RedisRateLimiter(self.redis_client, key, rate, burst)
                else:
                    self._limiters[key] = TokenBucketRateLimiter(rate, burst)
            return self._limiters[key]

# Global instance
registry = RateLimiterRegistry()

def rate_limited(calls_per_second: float = 2.0, burst: int = 5, key_prefix: str = "global"):
    """Decorator to apply rate limiting to async functions."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key = f"{key_prefix}:{func.__module__}:{func.__name__}"
            limiter = await registry.get_limiter(key, calls_per_second, burst)
            await limiter.acquire()
            return await func(*args, **kwargs)
        return wrapper
    return decorator
