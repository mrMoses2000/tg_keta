"""
Unit tests for webhook idempotency and enqueue behavior.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.bot import webhook


class _DummyRequest:
    def __init__(self, headers: dict[str, str], payload: dict | None = None, json_exc: Exception | None = None):
        self.headers = headers
        self._payload = payload or {}
        self._json_exc = json_exc

    async def json(self) -> dict:
        if self._json_exc:
            raise self._json_exc
        return self._payload


def _valid_update() -> dict:
    return {
        "update_id": 1001,
        "message": {
            "message_id": 10,
            "date": 1700000000,
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 777, "is_bot": False, "first_name": "Ivan"},
            "text": "Хочу рецепт",
        },
    }


@pytest.mark.asyncio
async def test_webhook_duplicate_update_is_skipped(monkeypatch: pytest.MonkeyPatch):
    mark_received = AsyncMock(return_value=False)
    enqueue_job = AsyncMock()
    insert_inbound = AsyncMock()

    monkeypatch.setattr(webhook.pg, "mark_update_received", mark_received)
    monkeypatch.setattr(webhook.pg, "insert_inbound_event", insert_inbound)
    monkeypatch.setattr(webhook.rc, "enqueue_job", enqueue_job)

    req = _DummyRequest(
        headers={"X-Telegram-Bot-Api-Secret-Token": webhook.settings.telegram_webhook_secret},
        payload=_valid_update(),
    )
    resp = await webhook.handle_webhook(req)

    assert resp.status == 200
    assert json.loads(resp.text) == {"ok": True}
    mark_received.assert_awaited_once_with(1001)
    insert_inbound.assert_not_awaited()
    enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_first_seen_update_is_enqueued(monkeypatch: pytest.MonkeyPatch):
    mark_received = AsyncMock(return_value=True)
    insert_inbound = AsyncMock(return_value="inbound-id-1")
    enqueue_job = AsyncMock()

    monkeypatch.setattr(webhook.pg, "mark_update_received", mark_received)
    monkeypatch.setattr(webhook.pg, "insert_inbound_event", insert_inbound)
    monkeypatch.setattr(webhook.rc, "enqueue_job", enqueue_job)

    req = _DummyRequest(
        headers={"X-Telegram-Bot-Api-Secret-Token": webhook.settings.telegram_webhook_secret},
        payload=_valid_update(),
    )
    resp = await webhook.handle_webhook(req)

    assert resp.status == 200
    mark_received.assert_awaited_once_with(1001)
    insert_inbound.assert_awaited_once()
    enqueue_job.assert_awaited_once()

    enqueued_job = enqueue_job.await_args.args[0]
    assert enqueued_job["update_id"] == 1001
    assert enqueued_job["chat_id"] == 12345
    assert enqueued_job["user_id"] == 777
