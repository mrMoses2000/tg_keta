"""
Unit tests for actions_applier safety around state transitions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.llm.actions_applier import apply_actions
from src.models import ActionsJson, ConversationState, UserProfile


def _profile() -> UserProfile:
    return UserProfile(id="00000000-0000-0000-0000-000000000001", telegram_id=321)


def _state(mode: str = "onboarding") -> ConversationState:
    return ConversationState(
        user_id="00000000-0000-0000-0000-000000000001",
        telegram_chat_id=123,
        mode=mode,
        step="ask_restrictions",
    )


@pytest.mark.asyncio
async def test_invalid_state_transition_is_ignored(monkeypatch: pytest.MonkeyPatch):
    upsert_state = AsyncMock()
    monkeypatch.setattr("src.llm.actions_applier.pg.upsert_conversation_state", upsert_state)
    monkeypatch.setattr("src.llm.actions_applier.supa.update_user_profile", lambda *_args, **_kwargs: None)

    actions = ActionsJson.model_validate(
        {
            "reply_text": "ok",
            "actions": {
                "state_patch": {"mode": "coaching", "step": "suggesting_alternative"},
            },
        }
    )

    await apply_actions(actions, _profile(), _state("onboarding"), chat_id=123)

    upsert_state.assert_awaited_once()
    kwargs = upsert_state.await_args.kwargs
    assert kwargs["mode"] == "onboarding"
    assert kwargs["step"] == "suggesting_alternative"


@pytest.mark.asyncio
async def test_valid_state_transition_is_applied(monkeypatch: pytest.MonkeyPatch):
    upsert_state = AsyncMock()
    monkeypatch.setattr("src.llm.actions_applier.pg.upsert_conversation_state", upsert_state)
    monkeypatch.setattr("src.llm.actions_applier.supa.update_user_profile", lambda *_args, **_kwargs: None)

    actions = ActionsJson.model_validate(
        {
            "reply_text": "ok",
            "actions": {
                "state_patch": {"mode": "recipe_search", "step": "showing_results"},
            },
        }
    )

    await apply_actions(actions, _profile(), _state("onboarding"), chat_id=123)

    kwargs = upsert_state.await_args.kwargs
    assert kwargs["mode"] == "recipe_search"
    assert kwargs["step"] == "showing_results"
