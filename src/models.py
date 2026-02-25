"""
tg_keto.models — Pydantic models for the entire data pipeline.

Covers:
  - Telegram update parsing (TelegramUpdate → Job)
  - LLM output contract (ActionsJson, ProfilePatch, StatePatch, RecipeQuery, SafetyFlags)
  - Internal state (ConversationState, UserProfile)

Design notes:
  - Every field has explicit types and defaults.
  - Allowlist validation is enforced via Literal types and field constraints.
  - LLM output is validated AFTER parsing; invalid JSON → fallback safe reply.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ─── Telegram Update (inbound) ──────────────────────────────────────────

class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class TelegramChat(BaseModel):
    id: int
    type: str = "private"
    first_name: str | None = None
    username: str | None = None


class TelegramMessage(BaseModel):
    message_id: int
    date: int
    chat: TelegramChat
    from_user: TelegramUser | None = Field(None, alias="from")
    text: str | None = None

    model_config = {"populate_by_name": True}


class TelegramCallbackQuery(BaseModel):
    id: str
    from_user: TelegramUser = Field(..., alias="from")
    data: str | None = None
    message: TelegramMessage | None = None

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    """Minimal Telegram Update model — only fields we need."""
    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None

    @property
    def effective_chat_id(self) -> int | None:
        if self.message:
            return self.message.chat.id
        if self.callback_query and self.callback_query.message:
            return self.callback_query.message.chat.id
        return None

    @property
    def effective_user_id(self) -> int | None:
        if self.message and self.message.from_user:
            return self.message.from_user.id
        if self.callback_query:
            return self.callback_query.from_user.id
        return None

    @property
    def effective_text(self) -> str | None:
        if self.message and self.message.text:
            return self.message.text
        if self.callback_query and self.callback_query.data:
            return self.callback_query.data
        return None


# ─── Job (internal queue item) ───────────────────────────────────────────

class Job(BaseModel):
    """Queued work item: one inbound message to process."""
    update_id: int
    chat_id: int
    user_id: int
    text: str
    raw_update: dict[str, Any]
    received_at: datetime = Field(default_factory=datetime.utcnow)
    attempt: int = 0


# ─── LLM Output Contract ────────────────────────────────────────────────

ALLOWED_MODES = Literal["idle", "onboarding", "recipe_search", "consultation", "coaching"]
ALLOWED_STEPS = Literal[
    "ask_restrictions", "ask_taste", "ask_goals",
    "showing_results", "explaining", "suggesting_alternative",
    None,
]
ALLOWED_CATEGORIES = Literal[
    "завтрак", "обед", "ужин", "перекус", "десерт", "салат", "суп", None
]
ALLOWED_TASTES = Literal["sweet", "salty", "spicy", "sour", None]
ALLOWED_RED_FLAGS = Literal[
    "chest_pain", "hypoglycemia", "dehydration", "confusion", "other", None
]

PROFILE_PATCH_ALLOWED_KEYS = frozenset({
    "taste_preferences", "diabetes_type", "lactose_intolerant",
    "allergies_detail", "weight_kg", "target_weight_kg",
    "dietary_restrictions", "bot_onboarding_completed",
})


class ProfilePatch(BaseModel):
    """Validated subset of user profile fields that LLM may suggest updating."""
    taste_preferences: list[str] | None = None
    diabetes_type: str | None = None
    lactose_intolerant: bool | None = None
    allergies_detail: list[dict[str, str]] | None = None
    dietary_restrictions: list[str] | None = None
    weight_kg: float | None = Field(None, ge=20, le=400)
    target_weight_kg: float | None = Field(None, ge=20, le=400)
    bot_onboarding_completed: bool | None = None


class StatePatch(BaseModel):
    """Validated FSM state transition proposed by LLM."""
    mode: ALLOWED_MODES | None = None
    step: ALLOWED_STEPS | None = None


class RecipeQuery(BaseModel):
    """Recipe search parameters proposed by LLM."""
    category: ALLOWED_CATEGORIES = None
    exclude_ingredients: list[str] = Field(default_factory=list, max_length=20)
    taste: ALLOWED_TASTES = None
    max_cooking_time: int | None = Field(None, ge=5, le=240)
    limit: int = Field(5, ge=1, le=10)


class SafetyFlags(BaseModel):
    """Safety classification of the user message."""
    medical_concern: bool = False
    off_topic: bool = False
    red_flag_type: ALLOWED_RED_FLAGS = None


class ActionsJson(BaseModel):
    """
    The strict contract for LLM output.
    LLM returns this JSON; our code validates and applies.
    """
    reply_text: str = Field(..., min_length=1, max_length=4000)
    actions: Actions | None = None


class Actions(BaseModel):
    profile_patch: ProfilePatch | None = None
    state_patch: StatePatch | None = None
    recipe_query: RecipeQuery | None = None
    safety_flags: SafetyFlags | None = None


# ─── User Profile (from Supabase users table) ────────────────────────────

class UserProfile(BaseModel):
    """User record from Supabase users table."""
    id: str
    telegram_id: int
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    language_code: str = "ru"
    weight_kg: float | None = None
    target_weight_kg: float | None = None
    height_cm: int | None = None
    birth_date: str | None = None
    gender: str | None = None
    activity_level: str = "moderate"
    health_goals: list[str] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)
    diabetes_type: str | None = None
    lactose_intolerant: bool = False
    allergies_detail: list[dict[str, str]] = Field(default_factory=list)
    taste_preferences: list[str] = Field(default_factory=list)
    bot_onboarding_completed: bool = False
    is_blocked: bool = False
    created_at: str | None = None


# ─── Conversation State (from local Postgres) ────────────────────────────

class ConversationState(BaseModel):
    """FSM state for a user's active conversation."""
    user_id: str
    telegram_chat_id: int
    mode: str = "idle"
    step: str | None = None
    context_summary: dict[str, Any] = Field(default_factory=dict)
    last_messages: list[dict[str, Any]] = Field(default_factory=list)


# ─── Recipe (from Supabase) ──────────────────────────────────────────────

class RecipeIngredient(BaseModel):
    name: str
    amount: str = ""
    unit: str = ""


class RecipeMacros(BaseModel):
    calories: float = 0
    protein: float = 0
    fat: float = 0
    carbs: float = 0
    fiber: float = 0


class Recipe(BaseModel):
    """Recipe record from Supabase recipes table."""
    id: str
    title: str
    description: str | None = None
    image_url: str | None = None
    cooking_time: int | None = None
    cooking_time_text: str | None = None
    servings: int = 2
    category: str = "обед"
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    instructions: list[dict[str, Any]] = Field(default_factory=list)
    macros: RecipeMacros = Field(default_factory=RecipeMacros)
    tags: list[str] = Field(default_factory=list)

    @field_validator("ingredients", mode="before")
    @classmethod
    def _parse_ingredients(cls, v: Any) -> list[dict]:
        """Handle JSONB array from Supabase."""
        if isinstance(v, list):
            return v
        return []

    @field_validator("macros", mode="before")
    @classmethod
    def _parse_macros(cls, v: Any) -> dict:
        """Handle JSONB object from Supabase."""
        if isinstance(v, dict):
            return v
        return {}
