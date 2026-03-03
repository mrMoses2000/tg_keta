"""
Unit tests for worker lock requeue behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.queue import worker


def _base_job(attempt: int = 0) -> dict:
    return {
        "update_id": 2001,
        "chat_id": 10,
        "user_id": 20,
        "text": "Привет",
        "raw_update": {"update_id": 2001},
        "attempt": attempt,
    }


@pytest.mark.asyncio
async def test_process_job_requeues_with_incremented_attempt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worker.rc, "acquire_user_lock", AsyncMock(return_value=False))
    enqueue_job = AsyncMock()
    mark_failed = AsyncMock()
    sleep_mock = AsyncMock()

    monkeypatch.setattr(worker.rc, "enqueue_job", enqueue_job)
    monkeypatch.setattr(worker.pg, "mark_update_failed", mark_failed)
    monkeypatch.setattr(worker.asyncio, "sleep", sleep_mock)

    await worker.process_job(_base_job(attempt=0))

    enqueue_job.assert_awaited_once()
    mark_failed.assert_not_awaited()
    payload = enqueue_job.await_args.args[0]
    assert payload["attempt"] == 1
    sleep_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_process_job_drops_after_max_requeue_attempts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worker.rc, "acquire_user_lock", AsyncMock(return_value=False))
    enqueue_job = AsyncMock()
    mark_failed = AsyncMock()
    sleep_mock = AsyncMock()

    monkeypatch.setattr(worker.rc, "enqueue_job", enqueue_job)
    monkeypatch.setattr(worker.pg, "mark_update_failed", mark_failed)
    monkeypatch.setattr(worker.asyncio, "sleep", sleep_mock)

    await worker.process_job(_base_job(attempt=worker.MAX_LOCK_REQUEUE_ATTEMPTS))

    enqueue_job.assert_not_awaited()
    mark_failed.assert_awaited_once_with(2001)
    sleep_mock.assert_not_awaited()
