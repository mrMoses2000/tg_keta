"""
tg_keto.engine.outbox_dispatcher — Retry delivery of pending outbox messages.

The outbox pattern guarantees message delivery even if:
  - Telegram API is temporarily down (503, timeout)
  - Our process crashes after TX2 commit but before sendMessage
  - Network blip during delivery

How it works:
  1. Query local Postgres for outbound_events with status='pending' and attempts < 5
  2. For each: POST to Telegram sendMessage
  3. On success: mark as 'sent'
  4. On failure: increment attempts, mark as 'failed' (will retry next sweep)
  5. After 5 failed attempts: give up (manual intervention needed)

This runs as a periodic task (every 10-30 seconds) alongside the main worker.
"""

from __future__ import annotations

import asyncio

import structlog

from src.db import postgres as pg
from src.bot import telegram_sender as tg

logger = structlog.get_logger(__name__)


async def dispatch_pending(batch_size: int = 10) -> int:
    """
    Dispatch pending outbox messages to Telegram.
    Returns the number of successfully sent messages.
    """
    events = await pg.get_pending_outbox(limit=batch_size)
    if not events:
        return 0

    sent_count = 0
    for event in events:
        try:
            result = await tg.send_message(
                chat_id=event["chat_id"],
                text=event["reply_text"],
                reply_markup=event.get("reply_markup"),
            )

            if result and result.get("ok"):
                await pg.mark_outbound_sent(event["id"])
                sent_count += 1
                logger.info(
                    "outbox_sent",
                    event_id=event["id"],
                    chat_id=event["chat_id"],
                    attempt=event["attempts"] + 1,
                )
            else:
                error_msg = result.get("description", "Unknown error") if result else "No response"
                await pg.mark_outbound_failed(event["id"], error_msg)
                logger.warning(
                    "outbox_send_failed",
                    event_id=event["id"],
                    error=error_msg,
                    attempt=event["attempts"] + 1,
                )

        except Exception as e:
            await pg.mark_outbound_failed(event["id"], str(e))
            logger.error(
                "outbox_dispatch_error",
                event_id=event["id"],
                error=str(e),
            )

    return sent_count


async def run_outbox_loop(interval: int = 15) -> None:
    """
    Periodic loop that dispatches pending outbox messages.
    Runs indefinitely until cancelled.
    """
    logger.info("outbox_loop_started", interval_seconds=interval)
    while True:
        try:
            sent = await dispatch_pending()
            if sent > 0:
                logger.info("outbox_sweep_complete", sent=sent)
        except Exception as e:
            logger.error("outbox_sweep_error", error=str(e))

        await asyncio.sleep(interval)
