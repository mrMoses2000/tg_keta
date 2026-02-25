"""
tg_keto.bot.telegram_sender — HTTP client for Telegram Bot API.

We use raw aiohttp requests instead of python-telegram-bot library
for three reasons:
  1. Lighter: no framework overhead, just HTTP POST calls
  2. Async: native aiohttp, no thread bridges
  3. Control: we decide retry and timeout behavior

Under the hood:
  sendMessage → POST https://api.telegram.org/bot{token}/sendMessage
  Body: JSON { chat_id, text, parse_mode, reply_markup }
  Telegram returns: { ok: true, result: Message }
"""

from __future__ import annotations

import aiohttp
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

BASE_URL = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(
    chat_id: int,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> dict | None:
    """
    Send a text message via Telegram Bot API.
    Returns the API response dict, or None on network failure.
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    return await _post("sendMessage", payload)


async def send_chat_action(chat_id: int, action: str = "typing") -> None:
    """Send a 'typing...' indicator to the user."""
    await _post("sendChatAction", {"chat_id": chat_id, "action": action})


async def edit_message(
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: str = "HTML",
) -> dict | None:
    """Edit an existing message (e.g. replace placeholder with real reply)."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    return await _post("editMessageText", payload)


async def set_webhook(url: str, secret_token: str) -> bool:
    """Register the webhook URL with Telegram."""
    result = await _post("setWebhook", {
        "url": url,
        "secret_token": secret_token,
        "allowed_updates": ["message", "callback_query"],
    })
    if result and result.get("ok"):
        logger.info("webhook_registered", url=url)
        return True
    logger.error("webhook_registration_failed", result=result)
    return False


async def _post(method: str, payload: dict) -> dict | None:
    """Internal: POST to Telegram Bot API with error handling."""
    url = f"{BASE_URL}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(
                        "telegram_api_error",
                        method=method,
                        status=resp.status,
                        error=data.get("description"),
                    )
                return data
    except aiohttp.ClientError as e:
        logger.error("telegram_api_network_error", method=method, error=str(e))
        return None
    except Exception as e:
        logger.error("telegram_api_unexpected_error", method=method, error=str(e))
        return None
