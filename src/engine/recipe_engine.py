"""
tg_keto.engine.recipe_engine — Recipe filtering, ranking, and selection.

This module selects recipes BEFORE they go to LLM.
The LLM only sees top-N pre-filtered recipes (not all 430+).
This saves tokens and improves response quality.

Filtering pipeline:
  1. Build exclusion list from user profile (dietary_restrictions, allergies, lactose)
  2. Build preference criteria (taste, category, cooking time)
  3. Query Supabase via supabase_client (uses RPC for ingredient exclusion)
  4. Score and rank results
  5. Return top-N (default 5)

Caching:
  Recipe query results are cached in Redis (key = hash of filter params).
  TTL = RECIPE_CACHE_TTL_SECONDS (default 300s / 5min).
"""

from __future__ import annotations

import hashlib
import json

import structlog

from src.config import settings
from src.models import UserProfile, Recipe, RecipeQuery
from src.db import supabase_client as supa
from src.db import redis_client as rc

logger = structlog.get_logger(__name__)

# Map profile restrictions to ingredient exclusion stems
# (matched via ILIKE in Supabase RPC, same logic as reference project)
RESTRICTION_TO_EXCLUSION: dict[str, list[str]] = {
    "dairy_free": ["молоко", "сливк", "сметан", "творог", "сыр", "масло сливочн"],
    "lactose_free": ["молоко", "сливк", "сметан", "творог", "кефир", "йогурт"],
    "nut_free": ["орех", "миндал", "фундук", "кешью", "пекан", "грецк"],
    "egg_free": ["яйц", "яйцо"],
    "gluten_free": ["пшениц", "мук пшеничн", "хлеб", "панировк"],
    "pork_free": ["свинин", "бекон", "сало"],
    "soy_free": ["соя", "соев"],
    "fish_free": ["рыб", "лосос", "тунец", "сёмг", "треск"],
    "shellfish_free": ["креветк", "краб", "мидии", "кальмар"],
}


async def find_recipes(
    profile: UserProfile,
    query: RecipeQuery | None = None,
) -> list[Recipe]:
    """
    Find recipes matching user's profile restrictions and query preferences.

    Steps:
      1. Build exclusion list from profile
      2. Merge with query-specific exclusions
      3. Check cache
      4. Query Supabase
      5. Score and rank
      6. Cache result
      7. Return top-N
    """
    # 1. Build exclusions from profile
    exclusions = _build_exclusions(profile)

    # 2. Merge query-specific exclusions
    category = None
    max_time = None
    limit = 5

    if query:
        exclusions.extend(query.exclude_ingredients)
        category = query.category
        max_time = query.max_cooking_time
        limit = query.limit

    # Deduplicate
    exclusions = list(set(exclusions))

    # 3. Check cache
    cache_key = _cache_key(category, exclusions, max_time)
    cached = await rc.get_cached(cache_key)
    if cached:
        logger.debug("recipe_cache_hit", key=cache_key)
        recipes = [Recipe.model_validate(r) for r in cached]
    else:
        # 4. Query Supabase
        logger.debug("recipe_cache_miss", key=cache_key, exclusions=exclusions)
        recipes = supa.get_recipes(
            category=category,
            excluded=exclusions if exclusions else None,
            limit=30,  # Fetch more than needed for scoring
        )

        # Filter by cooking time if specified
        if max_time:
            recipes = [r for r in recipes if r.cooking_time and r.cooking_time <= max_time]

        # 6. Cache
        if recipes:
            await rc.set_cached(
                cache_key,
                [r.model_dump(mode="json") for r in recipes],
                ttl=settings.recipe_cache_ttl_seconds,
            )

    # 5. Score and rank
    scored = _score_recipes(recipes, profile, query)

    # 7. Return top-N
    return scored[:limit]


def _build_exclusions(profile: UserProfile) -> list[str]:
    """Build ingredient exclusion stems from user profile restrictions."""
    exclusions: list[str] = []

    for restriction in profile.dietary_restrictions:
        if restriction in RESTRICTION_TO_EXCLUSION:
            exclusions.extend(RESTRICTION_TO_EXCLUSION[restriction])

    # Lactose intolerance
    if profile.lactose_intolerant:
        exclusions.extend(RESTRICTION_TO_EXCLUSION.get("lactose_free", []))

    # Explicit allergies
    for allergy in profile.allergies_detail:
        allergen = allergy.get("allergen", "")
        key = f"{allergen}_free"
        if key in RESTRICTION_TO_EXCLUSION:
            exclusions.extend(RESTRICTION_TO_EXCLUSION[key])

    return list(set(exclusions))


def _score_recipes(
    recipes: list[Recipe],
    profile: UserProfile,
    query: RecipeQuery | None,
) -> list[Recipe]:
    """
    Score and sort recipes by relevance.
    Higher score = better match.
    """
    scored: list[tuple[float, Recipe]] = []

    for recipe in recipes:
        score = 0.0

        # Prefer lower carbs (keto-friendly)
        if recipe.macros.carbs < 5:
            score += 3.0
        elif recipe.macros.carbs < 10:
            score += 2.0
        elif recipe.macros.carbs < 20:
            score += 1.0

        # Prefer reasonable cooking time
        if recipe.cooking_time and recipe.cooking_time <= 30:
            score += 1.0

        # Taste preference match (basic — expand with recipe_tags later)
        if query and query.taste:
            taste = query.taste
            title_lower = recipe.title.lower()
            desc_lower = (recipe.description or "").lower()
            combined = title_lower + " " + desc_lower
            taste_keywords = _taste_keywords(taste)
            if any(kw in combined for kw in taste_keywords):
                score += 2.0

        # Variety: slight randomization
        import random
        score += random.uniform(0, 0.5)

        scored.append((score, recipe))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [recipe for _, recipe in scored]


def _taste_keywords(taste: str) -> list[str]:
    """Map taste preference to Russian keywords for basic matching."""
    mapping = {
        "sweet": ["сладк", "десерт", "шоколад", "ваниль", "ягод", "мёд", "эритрит"],
        "salty": ["солён", "сыр", "бекон", "острый", "маринован"],
        "spicy": ["остр", "перец", "чили", "карри", "имбир", "паприк"],
        "sour": ["кисл", "лимон", "уксус", "квашен", "маринован"],
    }
    return mapping.get(taste, [])


def _cache_key(
    category: str | None,
    exclusions: list[str],
    max_time: int | None,
) -> str:
    """Generate a deterministic cache key from filter params."""
    data = json.dumps(
        {"c": category, "e": sorted(exclusions), "t": max_time},
        sort_keys=True,
    )
    h = hashlib.md5(data.encode()).hexdigest()[:12]
    return f"recipes:{h}"
