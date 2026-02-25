"""
tg_keto.db.redis_client — Redis connection for queue, cache, and distributed locks.

Architecture:
  Redis serves three roles:
  1. Queue: LPUSH/BRPOP on list 'queue:incoming' — job queue for workers
  2. Cache: GET/SET with TTL — recipe query results, reducing Supabase calls
  3. Lock:  SET NX EX  — per-user distributed lock for sequential processing

Under the hood:
  redis-py async uses a connection pool → socket to Redis server.
  BRPOP is a blocking read: the Redis server suspends the connection
  until data arrives (no busy polling, no CPU waste).
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

# Module-level client
_redis: aioredis.Redis | None = None

QUEUE_KEY = "queue:incoming"
LOCK_PREFIX = "lock:user:"
CACHE_PREFIX = "cache:recipes:"


async def get_redis() -> aioredis.Redis:
    """Get or create async Redis client."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
        # Verify connectivity
        await _redis.ping()
        logger.info("redis_connected", url=settings.redis_url)
    return _redis


async def close_redis() -> None:
    """Close Redis connection gracefully."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("redis_closed")


# ─── Queue Operations ───────────────────────────────────────────────────

async def enqueue_job(job: dict) -> None:
    """Push a job to the left of the queue list. Workers BRPOP from the right (FIFO)."""
    r = await get_redis()
    await r.lpush(QUEUE_KEY, json.dumps(job, ensure_ascii=False, default=str))
    logger.debug("job_enqueued", update_id=job.get("update_id"))


async def dequeue_job(timeout: int = 5) -> dict | None:
    """
    Blocking pop from the right of the queue (FIFO order).
    Returns None on timeout (no job available).
    Timeout prevents infinite blocking during shutdown.
    """
    r = await get_redis()
    result = await r.brpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    # result = (key, value)
    _, raw = result
    return json.loads(raw)


# ─── Distributed Lock (per-user) ────────────────────────────────────────

async def acquire_user_lock(user_id: int, ttl: int = 120) -> bool:
    """
    Try to acquire a per-user lock using SET NX EX.

    SET NX = set only if key does Not eXist (atomic)
    EX ttl = auto-expire after ttl seconds (safety against deadlocks)

    Returns True if lock acquired, False if another worker holds it.
    """
    r = await get_redis()
    key = f"{LOCK_PREFIX}{user_id}"
    acquired = await r.set(key, "locked", nx=True, ex=ttl)
    if acquired:
        logger.debug("user_lock_acquired", user_id=user_id, ttl=ttl)
    return bool(acquired)


async def release_user_lock(user_id: int) -> None:
    """Release the per-user lock."""
    r = await get_redis()
    key = f"{LOCK_PREFIX}{user_id}"
    await r.delete(key)
    logger.debug("user_lock_released", user_id=user_id)


# ─── Cache Operations ───────────────────────────────────────────────────

async def get_cached(key: str) -> Any | None:
    """Get a cached value by key. Returns None on miss."""
    r = await get_redis()
    raw = await r.get(f"{CACHE_PREFIX}{key}")
    if raw is None:
        return None
    return json.loads(raw)


async def set_cached(key: str, value: Any, ttl: int | None = None) -> None:
    """Set a cached value with optional TTL (seconds)."""
    r = await get_redis()
    ttl = ttl or settings.recipe_cache_ttl_seconds
    await r.set(
        f"{CACHE_PREFIX}{key}",
        json.dumps(value, ensure_ascii=False, default=str),
        ex=ttl,
    )
