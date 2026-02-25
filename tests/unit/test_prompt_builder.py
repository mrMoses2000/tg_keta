"""
Unit tests for prompt builder.
"""
import pytest
from src.llm.prompt_builder import build_prompt, _build_profile_context, _build_recipes_context
from src.models import UserProfile, ConversationState, Recipe, RecipeMacros, RecipeIngredient


class TestBuildPrompt:
    """Test full prompt assembly."""

    def test_minimal_prompt(self):
        prompt = build_prompt("Привет", None, None, None)
        assert "КетоБот" in prompt
        assert "СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ" in prompt
        assert "Привет" in prompt

    def test_with_profile(self):
        profile = UserProfile(
            id="test-id",
            telegram_id=123,
            telegram_first_name="Анна",
            weight_kg=70,
            dietary_restrictions=["dairy_free"],
            lactose_intolerant=True,
        )
        prompt = build_prompt("рецепт", profile, None, None)
        assert "Анна" in prompt
        assert "70" in prompt
        assert "лактоз" in prompt.lower()

    def test_with_recipes(self):
        recipes = [
            Recipe(
                id="r1",
                title="Кето-оладьи",
                category="завтрак",
                cooking_time_text="15 мин",
                macros=RecipeMacros(calories=250, protein=10, fat=20, carbs=3),
                ingredients=[RecipeIngredient(name="Миндальная мука")],
            ),
        ]
        prompt = build_prompt("завтрак", None, None, recipes)
        assert "Кето-оладьи" in prompt
        assert "250" in prompt  # calories


class TestProfileContext:
    """Test profile context formatting."""

    def test_empty_profile(self):
        profile = UserProfile(id="x", telegram_id=1)
        ctx = _build_profile_context(profile)
        assert "не заполнен" in ctx


class TestRecipesContext:
    """Test recipe context formatting."""

    def test_empty_recipes(self):
        ctx = _build_recipes_context([])
        assert "нет" in ctx.lower()

    def test_truncation(self):
        recipes = [
            Recipe(
                id=f"r{i}",
                title=f"Рецепт {i}",
                macros=RecipeMacros(calories=100*i),
                ingredients=[RecipeIngredient(name=f"Ингр {j}") for j in range(15)],
            )
            for i in range(5)
        ]
        ctx = _build_recipes_context(recipes)
        assert "5 шт." in ctx
        assert "ещё" in ctx  # truncated ingredients indicator
