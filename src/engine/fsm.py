"""
tg_keto.engine.fsm — Conversation state machine.

States:
  idle         → User has no active flow. Default state.
  onboarding   → Collecting profile data (restrictions, goals, preferences).
  recipe_search → Actively looking for recipes.
  consultation → Answering keto questions (not recipe search).
  coaching     → Motivational support mode.

Transitions:
  - /start → onboarding (if profile incomplete) or idle
  - User asks for recipe → recipe_search
  - User asks keto question → consultation
  - User needs motivation → coaching
  - Any state → idle (on explicit "done" or after prolonged inactivity)

The FSM is "advisory": LLM proposes transitions via state_patch,
our code validates and applies. Invalid transitions are silently ignored.
"""

from __future__ import annotations

import structlog

from src.models import ConversationState

logger = structlog.get_logger(__name__)

# Valid state transitions: {from_state: {allowed_next_states}}
VALID_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"onboarding", "recipe_search", "consultation", "coaching"},
    "onboarding": {"idle", "recipe_search", "consultation"},
    "recipe_search": {"idle", "consultation", "coaching", "recipe_search"},
    "consultation": {"idle", "recipe_search", "coaching", "consultation"},
    "coaching": {"idle", "recipe_search", "consultation", "coaching"},
}


def is_valid_transition(current_mode: str, next_mode: str) -> bool:
    """Check if a state transition is valid."""
    if current_mode == next_mode:
        return True  # staying in same mode is always valid
    allowed = VALID_TRANSITIONS.get(current_mode, set())
    return next_mode in allowed


def determine_initial_mode(profile_complete: bool) -> str:
    """Determine the initial mode for a new or returning user."""
    if not profile_complete:
        return "onboarding"
    return "idle"


def should_offer_onboarding(state: ConversationState, profile_complete: bool) -> bool:
    """Check if we should suggest onboarding to the user."""
    return not profile_complete and state.mode != "onboarding"


def update_last_messages(
    state: ConversationState,
    user_message: str,
    assistant_reply: str,
    max_messages: int = 10,
) -> list[dict]:
    """
    Append user + assistant messages to the bounded ring buffer.
    Keeps only the last `max_messages` entries.
    """
    from datetime import datetime

    messages = list(state.last_messages)  # copy
    messages.append({
        "role": "user",
        "content": user_message,
        "ts": datetime.utcnow().isoformat(),
    })
    messages.append({
        "role": "assistant",
        "content": assistant_reply[:500],  # truncate assistant reply for storage
        "ts": datetime.utcnow().isoformat(),
    })

    # Keep only last N
    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    return messages
