"""
tg_keto.llm.prompt_builder — Assemble system + user prompt for LLM.

The prompt is structured as:
  1. SYSTEM: persona, rules, constraints, safety, output format
  2. PROFILE: user's dietary restrictions, allergies, goals, preferences
  3. CONVERSATION STATE: current FSM mode, recent messages summary
  4. RECIPES: top-N pre-filtered recipes (compact, not full text)
  5. USER MESSAGE: the actual question/request

Design principles:
  - Keep prompt under ~6000 tokens (approximate) for Gemini CLI.
  - Recipes are pre-filtered in recipe_engine.py; only top-N go to LLM.
  - Profile and state are deterministic (loaded from DB, not generated).
  - Output format is strict JSON (actions_json contract).
"""

from __future__ import annotations

from src.models import UserProfile, ConversationState, Recipe

SYSTEM_PROMPT_RU = """Ты — дружелюбный коуч по кето-диете. Тебя зовут КетоБот.

ПРАВИЛА:
1. Ты помогаешь с кето-диетой, подбором рецептов и здоровым питанием.
2. Ты учитываешь ограничения клиента: аллергии, непереносимость лактозы, диабет, вкусовые предпочтения.
3. Ты НЕ врач. Не ставишь диагнозы, не назначаешь лекарства, не интерпретируешь анализы.
4. При опасных симптомах рекомендуешь обратиться к врачу/скорой.
5. Ты НЕ решаешь задачи по математике, программированию, юриспруденции, финансам, политике.
6. Ты отвечаешь по-русски, кратко и по делу.
7. Когда предлагаешь рецепт, объясни ПОЧЕМУ он подходит (макросы, ингредиенты, ограничения).
8. Если продукт нельзя — предложи ЗАМЕНУ.
9. Поддерживай мягкий мотивационный тон: кето — это не диета, а образ жизни.
10. Используй эмодзи умеренно для дружелюбности.

ФОРМАТ ОТВЕТА:
Ты ОБЯЗАН ответить СТРОГО в JSON формате:
{
  "reply_text": "Твой ответ пользователю",
  "actions": {
    "profile_patch": null,
    "state_patch": null,
    "recipe_query": null,
    "safety_flags": null
  }
}

ПРАВИЛА JSON:
- reply_text: строка 1-2000 символов, не пустая
- profile_patch: если пользователь сообщил новые данные о себе (аллергия, вес, предпочтения), укажи:
  {"taste_preferences": ["sweet"], "lactose_intolerant": true, ...}
  Допустимые поля: taste_preferences, diabetes_type, lactose_intolerant, allergies_detail, dietary_restrictions, weight_kg, target_weight_kg, bot_onboarding_completed
- state_patch: {"mode": "recipe_search", "step": "showing_results"}
  mode: idle | onboarding | recipe_search | consultation | coaching
- recipe_query: если нужно найти рецепты:
  {"category": "завтрак", "exclude_ingredients": ["молоко"], "taste": "sweet", "max_cooking_time": 30, "limit": 5}
- safety_flags: {"medical_concern": false, "off_topic": false, "red_flag_type": null}

Если сомневаешься — НЕ обновляй profile_patch и state_patch, просто ответь текстом."""


def build_prompt(
    user_message: str,
    profile: UserProfile | None,
    state: ConversationState | None,
    recipes: list[Recipe] | None = None,
) -> str:
    """
    Build the full prompt for LLM CLI.

    Returns a single string combining system prompt, user context,
    available recipes, and the user's message.
    """
    parts: list[str] = []

    # 1. System prompt
    parts.append(SYSTEM_PROMPT_RU)

    # 2. User profile context
    if profile:
        parts.append(_build_profile_context(profile))

    # 3. Conversation state
    if state:
        parts.append(_build_state_context(state))

    # 4. Available recipes
    if recipes:
        parts.append(_build_recipes_context(recipes))

    # 5. User message
    parts.append(f"\nСООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:\n{user_message}")

    return "\n\n".join(parts)


def _build_profile_context(profile: UserProfile) -> str:
    """Format user profile as compact text for LLM context."""
    lines = ["ПРОФИЛЬ КЛИЕНТА:"]

    if profile.telegram_first_name:
        lines.append(f"- Имя: {profile.telegram_first_name}")
    if profile.weight_kg:
        lines.append(f"- Вес: {profile.weight_kg} кг")
    if profile.target_weight_kg:
        lines.append(f"- Целевой вес: {profile.target_weight_kg} кг")
    if profile.height_cm:
        lines.append(f"- Рост: {profile.height_cm} см")
    if profile.gender:
        lines.append(f"- Пол: {profile.gender}")
    if profile.dietary_restrictions:
        lines.append(f"- Ограничения: {', '.join(profile.dietary_restrictions)}")
    if profile.lactose_intolerant:
        lines.append("- Непереносимость лактозы: да")
    if profile.diabetes_type:
        lines.append(f"- Диабет: {profile.diabetes_type}")
    if profile.allergies_detail:
        allergens = [a.get("allergen", "") for a in profile.allergies_detail]
        lines.append(f"- Аллергии: {', '.join(allergens)}")
    if profile.taste_preferences:
        lines.append(f"- Вкусовые предпочтения: {', '.join(profile.taste_preferences)}")
    if profile.health_goals:
        lines.append(f"- Цели: {', '.join(profile.health_goals)}")

    if len(lines) == 1:
        lines.append("- Профиль ещё не заполнен (предложи заполнить)")

    return "\n".join(lines)


def _build_state_context(state: ConversationState) -> str:
    """Format conversation state for LLM context."""
    lines = [f"СОСТОЯНИЕ ДИАЛОГА: mode={state.mode}"]
    if state.step:
        lines[0] += f", step={state.step}"

    # Include last few messages as conversation context
    if state.last_messages:
        lines.append("Последние сообщения:")
        for msg in state.last_messages[-5:]:  # last 5 messages max
            role = msg.get("role", "?")
            content = msg.get("content", "")
            # Truncate long messages
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"  [{role}]: {content}")

    return "\n".join(lines)


def _build_recipes_context(recipes: list[Recipe]) -> str:
    """
    Format pre-filtered recipes as compact text for LLM context.
    Only include essential info to minimize tokens.
    """
    if not recipes:
        return "ДОСТУПНЫЕ РЕЦЕПТЫ: нет подходящих рецептов в базе."

    lines = [f"ДОСТУПНЫЕ РЕЦЕПТЫ ({len(recipes)} шт.):"]
    for i, r in enumerate(recipes, 1):
        ingredients_names = [ing.name for ing in r.ingredients[:8]]  # max 8
        ingredients_str = ", ".join(ingredients_names)
        if len(r.ingredients) > 8:
            ingredients_str += f" и ещё {len(r.ingredients) - 8}"

        lines.append(
            f"{i}. {r.title} ({r.category}, {r.cooking_time_text or '?'})\n"
            f"   КБЖУ: {r.macros.calories} ккал, Б{r.macros.protein} Ж{r.macros.fat} У{r.macros.carbs}\n"
            f"   Ингредиенты: {ingredients_str}"
        )

    return "\n".join(lines)
