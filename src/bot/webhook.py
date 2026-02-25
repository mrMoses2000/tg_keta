"""
tg_keto.bot.webhook — aiohttp-based HTTP server for Telegram webhooks.

Request flow:
  1. Telegram POSTs to https://domain/webhook with JSON body (Update object)
  2. We validate X-Telegram-Bot-Api-Secret-Token header
  3. Parse Update → extract chat_id, user_id, text
  4. Idempotency check: processed_updates table (skip if duplicate)
  5. Audit: insert inbound_events
  6. Mark received in processed_updates
  7. Enqueue Job to Redis
  8. Return 200 OK immediately (< 50ms target)

The webhook MUST be fast: no LLM, no heavy DB queries.
Everything after enqueue happens in the worker process.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from aiohttp import web
import structlog

from src.config import settings
from src.models import TelegramUpdate, Job
from src.db import postgres as pg
from src.db import redis_client as rc

logger = structlog.get_logger(__name__)

routes = web.RouteTableDef()


@routes.get("/health")
async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint for monitoring."""
    return web.json_response({"status": "ok", "service": "keto-bot-webhook"})


@routes.post(settings.webhook_path)
async def handle_webhook(request: web.Request) -> web.Response:
    """
    Main webhook handler.

    Validates secret token, parses update, checks idempotency,
    logs audit event, enqueues job, returns 200 OK.

    Telegram retries if we return non-200, so we MUST return 200
    even for "acceptable" errors (duplicate, blocked user, etc.)
    to prevent retry storms.
    """
    # ── Step 1: Validate secret token ──
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != settings.telegram_webhook_secret:
        logger.warning("webhook_invalid_secret")
        # Still return 200 to avoid Telegram retries with bad token
        return web.json_response({"ok": True})

    # ── Step 2: Parse body ──
    try:
        raw: dict[str, Any] = await request.json()
    except Exception:
        logger.warning("webhook_bad_json")
        return web.json_response({"ok": True})

    try:
        update = TelegramUpdate.model_validate(raw)
    except Exception as e:
        logger.warning("webhook_parse_error", error=str(e))
        return web.json_response({"ok": True})

    chat_id = update.effective_chat_id
    user_id = update.effective_user_id
    text = update.effective_text

    if chat_id is None or user_id is None:
        logger.debug("webhook_no_chat_or_user", update_id=update.update_id)
        return web.json_response({"ok": True})

    log = logger.bind(update_id=update.update_id, chat_id=chat_id, user_id=user_id)

    # ── Step 3: Idempotency check ──
    is_duplicate = await pg.check_processed_update(update.update_id)
    if is_duplicate:
        log.debug("webhook_duplicate_update")
        return web.json_response({"ok": True})

    # ── Step 4: Audit + mark received ──
    await pg.mark_update_received(update.update_id)
    inbound_id = await pg.insert_inbound_event(
        update_id=update.update_id,
        chat_id=chat_id,
        user_id=user_id,
        message_text=text,
        raw_update=raw,
    )

    # ── Step 5: Enqueue job ──
    job = Job(
        update_id=update.update_id,
        chat_id=chat_id,
        user_id=user_id,
        text=text or "",
        raw_update=raw,
    )
    await rc.enqueue_job(job.model_dump(mode="json"))

    log.info("webhook_enqueued", inbound_id=inbound_id)
    return web.json_response({"ok": True})


async def on_startup(app: web.Application) -> None:
    """Initialize connections on server start."""
    await pg.get_pool()
    await rc.get_redis()
    logger.info("webhook_startup_complete", port=settings.webhook_port)


async def on_shutdown(app: web.Application) -> None:
    """Clean up connections on server stop."""
    await pg.close_pool()
    await rc.close_redis()
    logger.info("webhook_shutdown_complete")


def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application()
    app.router.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


def run_webhook() -> None:
    """Entry point: run the webhook HTTP server."""
    app = create_app()
    logger.info("webhook_starting", port=settings.webhook_port, path=settings.webhook_path)
    web.run_app(
        app,
        host="0.0.0.0",
        port=settings.webhook_port,
        print=None,  # suppress default banner (we log ourselves)
    )
