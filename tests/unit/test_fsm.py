"""
Unit tests for FSM (conversation state machine).
"""
import pytest
from src.engine.fsm import is_valid_transition, determine_initial_mode, update_last_messages
from src.models import ConversationState


class TestFSMTransitions:
    """Test valid and invalid state transitions."""

    def test_idle_to_recipe_search(self):
        assert is_valid_transition("idle", "recipe_search") is True

    def test_idle_to_onboarding(self):
        assert is_valid_transition("idle", "onboarding") is True

    def test_onboarding_to_idle(self):
        assert is_valid_transition("onboarding", "idle") is True

    def test_self_transition(self):
        assert is_valid_transition("recipe_search", "recipe_search") is True

    def test_invalid_onboarding_to_coaching(self):
        # Onboarding should not go directly to coaching
        assert is_valid_transition("onboarding", "coaching") is False


class TestInitialMode:
    """Test initial mode determination."""

    def test_incomplete_profile(self):
        assert determine_initial_mode(False) == "onboarding"

    def test_complete_profile(self):
        assert determine_initial_mode(True) == "idle"


class TestUpdateLastMessages:
    """Test bounded message ring buffer."""

    def test_append_messages(self):
        state = ConversationState(
            user_id="abc",
            telegram_chat_id=123,
            last_messages=[],
        )
        result = update_last_messages(state, "Привет", "Здравствуйте!")
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_bounded_buffer(self):
        state = ConversationState(
            user_id="abc",
            telegram_chat_id=123,
            last_messages=[{"role": "user", "content": f"msg{i}"} for i in range(20)],
        )
        result = update_last_messages(state, "new", "reply", max_messages=10)
        assert len(result) == 10  # bounded
