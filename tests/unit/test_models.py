"""
Unit tests for Pydantic models and Telegram update parsing.
"""
import pytest
from src.models import (
    TelegramUpdate, TelegramMessage, TelegramChat, TelegramUser,
    Job, ActionsJson, Actions, ProfilePatch, StatePatch,
    RecipeQuery, SafetyFlags, Recipe, RecipeIngredient, RecipeMacros,
)


class TestTelegramUpdate:
    """Test parsing of Telegram Update objects."""

    def test_parse_text_message(self):
        raw = {
            "update_id": 12345,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": 67890, "type": "private"},
                "from": {"id": 11111, "is_bot": False, "first_name": "Анна"},
                "text": "Хочу рецепт на завтрак",
            },
        }
        update = TelegramUpdate.model_validate(raw)
        assert update.update_id == 12345
        assert update.effective_chat_id == 67890
        assert update.effective_user_id == 11111
        assert update.effective_text == "Хочу рецепт на завтрак"

    def test_parse_callback_query(self):
        raw = {
            "update_id": 12346,
            "callback_query": {
                "id": "cb1",
                "from": {"id": 22222, "is_bot": False, "first_name": "Борис"},
                "data": "category:завтрак",
                "message": {
                    "message_id": 2,
                    "date": 1700000001,
                    "chat": {"id": 67890, "type": "private"},
                },
            },
        }
        update = TelegramUpdate.model_validate(raw)
        assert update.effective_chat_id == 67890
        assert update.effective_user_id == 22222
        assert update.effective_text == "category:завтрак"

    def test_parse_empty_update(self):
        raw = {"update_id": 12347}
        update = TelegramUpdate.model_validate(raw)
        assert update.effective_chat_id is None
        assert update.effective_user_id is None
        assert update.effective_text is None


class TestJob:
    """Test Job queue item creation."""

    def test_create_job(self):
        job = Job(
            update_id=1,
            chat_id=123,
            user_id=456,
            text="Привет",
            raw_update={"update_id": 1},
        )
        assert job.update_id == 1
        assert job.attempt == 0
        assert job.text == "Привет"


class TestActionsJson:
    """Test LLM output contract validation."""

    def test_valid_actions_json(self):
        data = {
            "reply_text": "Вот рецепт для вас!",
            "actions": {
                "profile_patch": {"taste_preferences": ["sweet"]},
                "state_patch": {"mode": "recipe_search"},
                "recipe_query": {"category": "завтрак", "limit": 3},
                "safety_flags": {"medical_concern": False},
            },
        }
        result = ActionsJson.model_validate(data)
        assert result.reply_text == "Вот рецепт для вас!"
        assert result.actions.profile_patch.taste_preferences == ["sweet"]
        assert result.actions.state_patch.mode == "recipe_search"
        assert result.actions.recipe_query.category == "завтрак"

    def test_minimal_actions_json(self):
        data = {"reply_text": "Просто текст"}
        result = ActionsJson.model_validate(data)
        assert result.reply_text == "Просто текст"
        assert result.actions is None

    def test_invalid_empty_reply(self):
        with pytest.raises(Exception):
            ActionsJson.model_validate({"reply_text": ""})

    def test_invalid_reply_too_long(self):
        with pytest.raises(Exception):
            ActionsJson.model_validate({"reply_text": "x" * 5000})


class TestProfilePatch:
    """Test profile patch validation."""

    def test_valid_patch(self):
        patch = ProfilePatch(
            taste_preferences=["sweet", "salty"],
            lactose_intolerant=True,
            weight_kg=75.5,
        )
        assert patch.taste_preferences == ["sweet", "salty"]
        assert patch.lactose_intolerant is True

    def test_weight_range(self):
        with pytest.raises(Exception):
            ProfilePatch(weight_kg=5)  # too low
        with pytest.raises(Exception):
            ProfilePatch(weight_kg=500)  # too high


class TestRecipe:
    """Test recipe model parsing from Supabase data."""

    def test_parse_recipe(self):
        recipe = Recipe(
            id="abc-123",
            title="Кето-сырники",
            category="завтрак",
            cooking_time=30,
            ingredients=[
                {"name": "Творог", "amount": "200", "unit": "г"},
                {"name": "Яйцо", "amount": "1", "unit": "шт"},
            ],
            macros={"calories": 350, "protein": 25, "fat": 28, "carbs": 4},
        )
        assert recipe.title == "Кето-сырники"
        assert len(recipe.ingredients) == 2
        assert recipe.macros.calories == 350

    def test_parse_empty_ingredients(self):
        recipe = Recipe(id="def-456", title="Тест", ingredients=[])
        assert recipe.ingredients == []
