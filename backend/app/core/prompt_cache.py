"""
core/prompt_cache.py — Live prompt retrieval with Redis cache.

Bridges the prompt playground (Postgres) to the live question engine.

Design:
  - Active prompt content cached in Redis with 30s TTL.
  - Cache is invalidated immediately when admin activates a new version.
  - If no active version exists in Postgres, returns None (caller uses default).
  - If Redis or Postgres is unavailable, silently falls back to None.
  - Zero latency impact on hot path: Redis GET takes < 1ms.

Cache key: "prompt_override:{prompt_type}"
TTL: 30 seconds (worst case: 30s before new activation takes effect)
On activation: key deleted immediately — next request fetches fresh from Postgres.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 30          # seconds
_CACHE_PREFIX = "prompt_override:"


def _cache_key(prompt_type: str) -> str:
    return f"{_CACHE_PREFIX}{prompt_type}"


async def get_live_system_prompt(prompt_type: str) -> str | None:
    """
    Return the active system prompt for this type.

    Priority:
      1. Redis cache (< 1ms, TTL 30s)
      2. Postgres active version (populates cache)
      3. None → caller uses hardcoded default

    Called once per turn from question.py — must be fast and never raise.
    """
    try:
        from app.core.redis import _get_pool
        redis = _get_pool()

        key = _cache_key(prompt_type)
        cached = await redis.get(key)

        if cached is not None:
            # Empty string stored as sentinel means "no active override"
            return cached if cached else None

        # Cache miss — fetch from Postgres
        from app.db.persistence import get_active_system_prompt
        content = await get_active_system_prompt(prompt_type)

        # Store result in cache — empty string = "no override" sentinel
        await redis.setex(key, _CACHE_TTL, content or "")
        return content

    except Exception as exc:
        log.warning("prompt_cache.lookup_failed",
                    prompt_type=prompt_type, error=str(exc))
        return None  # Always fall back silently


async def invalidate_prompt_cache(prompt_type: str) -> None:
    """
    Delete the Redis cache entry for this prompt type.
    Called immediately when admin activates a new version.
    Next question turn will fetch fresh content from Postgres.
    """
    try:
        from app.core.redis import _get_pool
        redis = _get_pool()
        await redis.delete(_cache_key(prompt_type))
        log.info("prompt_cache.invalidated", prompt_type=prompt_type)
    except Exception as exc:
        log.warning("prompt_cache.invalidate_failed",
                    prompt_type=prompt_type, error=str(exc))
