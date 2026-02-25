"""
tg_keto.db.postgres — Local Postgres connection pool for state tables.

Architecture decision:
  State tables (conversation_state, inbound/outbound_events, processed_updates)
  live in LOCAL Postgres for low-latency transactional writes during message processing.
  Recipes and user profiles live in Supabase (see supabase_client.py).

Under the hood:
  asyncpg creates a pool of TCP connections (default 2-10) to Postgres.
  Each connection is a single TCP socket → kernel send/recv buffer → Postgres backend process.
  TX1 and TX2 in the worker use these connections for short transactional writes.
  Pool reuse avoids fork+handshake overhead per query (~1-5ms instead of ~50ms).
"""

from __future__ import annotations

import asyncpg
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

# Module-level pool — initialize once, reuse across workers
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the asyncpg connection pool."""
    global _pool
    if _pool is None:
        logger.info("pg_pool_creating", dsn=settings.postgres_dsn.replace(settings.postgres_password, "***"))
        _pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("pg_pool_created", min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    """Close the pool gracefully (used during shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("pg_pool_closed")


# ─── State Table Operations ─────────────────────────────────────────────

async def check_processed_update(update_id: int) -> bool:
    """
    Idempotency check: returns True if update_id was already processed.
    Uses the processed_updates table (PRIMARY KEY on telegram_update_id).
    """
    pool = await get_pool()
    row = await pool.fetchval(
        "SELECT 1 FROM processed_updates WHERE telegram_update_id = $1",
        update_id,
    )
    return row is not None


async def mark_update_received(update_id: int, worker_id: str = "webhook") -> None:
    """Insert into processed_updates with status='received'."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO processed_updates (telegram_update_id, status, worker_id)
        VALUES ($1, 'received', $2)
        ON CONFLICT (telegram_update_id) DO NOTHING
        """,
        update_id,
        worker_id,
    )


async def insert_inbound_event(
    update_id: int,
    chat_id: int,
    user_id: int,
    message_text: str | None,
    raw_update: dict,
) -> str:
    """Audit log: record the raw inbound Telegram update. Returns event id."""
    import json

    pool = await get_pool()
    row = await pool.fetchval(
        """
        INSERT INTO inbound_events
            (telegram_update_id, telegram_chat_id, telegram_user_id, message_text, raw_update)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING id::text
        """,
        update_id,
        chat_id,
        user_id,
        message_text,
        json.dumps(raw_update, ensure_ascii=False),
    )
    return row


async def insert_outbound_event(
    chat_id: int,
    reply_text: str,
    reply_markup: dict | None = None,
    inbound_event_id: str | None = None,
) -> str:
    """Create outbox entry with status='pending'. Returns event id."""
    import json

    pool = await get_pool()
    row = await pool.fetchval(
        """
        INSERT INTO outbound_events
            (telegram_chat_id, reply_text, reply_markup, status, inbound_event_id)
        VALUES ($1, $2, $3::jsonb, 'pending', $4::uuid)
        RETURNING id::text
        """,
        chat_id,
        reply_text,
        json.dumps(reply_markup) if reply_markup else None,
        inbound_event_id,
    )
    return row


async def mark_outbound_sent(event_id: str) -> None:
    """Update outbox entry to status='sent'."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE outbound_events
        SET status = 'sent', last_attempt_at = now(), attempts = attempts + 1
        WHERE id = $1::uuid
        """,
        event_id,
    )


async def mark_outbound_failed(event_id: str, error: str) -> None:
    """Update outbox entry to status='failed'."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE outbound_events
        SET status = 'failed', last_attempt_at = now(),
            attempts = attempts + 1, error_message = $2
        WHERE id = $1::uuid
        """,
        event_id,
        error,
    )


async def mark_update_completed(update_id: int) -> None:
    """Mark processed_updates as completed after TX2 commit."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE processed_updates
        SET status = 'completed', completed_at = now()
        WHERE telegram_update_id = $1
        """,
        update_id,
    )


async def mark_update_failed(update_id: int) -> None:
    """Mark processed_updates as failed."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE processed_updates
        SET status = 'failed', completed_at = now()
        WHERE telegram_update_id = $1
        """,
        update_id,
    )


# ─── Conversation State ─────────────────────────────────────────────────

async def get_conversation_state(user_id: str) -> dict | None:
    """Load conversation FSM state for a user."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT user_id, telegram_chat_id, mode, step,
               context_summary::text, last_messages::text, updated_at
        FROM conversation_state
        WHERE user_id = $1::uuid
        """,
        user_id,
    )
    if row is None:
        return None
    import json
    return {
        "user_id": str(row["user_id"]),
        "telegram_chat_id": row["telegram_chat_id"],
        "mode": row["mode"],
        "step": row["step"],
        "context_summary": json.loads(row["context_summary"] or "{}"),
        "last_messages": json.loads(row["last_messages"] or "[]"),
    }


async def upsert_conversation_state(
    user_id: str,
    chat_id: int,
    mode: str = "idle",
    step: str | None = None,
    context_summary: dict | None = None,
    last_messages: list | None = None,
) -> None:
    """Create or update conversation state."""
    import json

    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO conversation_state
            (user_id, telegram_chat_id, mode, step, context_summary, last_messages, updated_at)
        VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6::jsonb, now())
        ON CONFLICT (user_id) DO UPDATE SET
            telegram_chat_id = EXCLUDED.telegram_chat_id,
            mode = EXCLUDED.mode,
            step = EXCLUDED.step,
            context_summary = EXCLUDED.context_summary,
            last_messages = EXCLUDED.last_messages,
            updated_at = now()
        """,
        user_id,
        chat_id,
        mode,
        step,
        json.dumps(context_summary or {}, ensure_ascii=False),
        json.dumps(last_messages or [], ensure_ascii=False),
    )


async def get_pending_outbox(limit: int = 10) -> list[dict]:
    """Fetch pending outbox events for retry dispatch."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id::text, telegram_chat_id, reply_text, reply_markup::text, attempts
        FROM outbound_events
        WHERE status = 'pending' AND attempts < 5
        ORDER BY created_at ASC
        LIMIT $1
        """,
        limit,
    )
    import json
    return [
        {
            "id": row["id"],
            "chat_id": row["telegram_chat_id"],
            "reply_text": row["reply_text"],
            "reply_markup": json.loads(row["reply_markup"]) if row["reply_markup"] else None,
            "attempts": row["attempts"],
        }
        for row in rows
    ]
