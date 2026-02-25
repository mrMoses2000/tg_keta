"""
tg_keto.db.supabase_client — Supabase REST client for recipes and user profiles.

Architecture decision:
  Supabase is the source of truth for recipes + user profiles.
  We use supabase-py (Python SDK) which wraps PostgREST:
    supabase.table("recipes").select("*").eq("category", "завтрак")
       ↓
    HTTP GET https://<project>.supabase.co/rest/v1/recipes?select=*&category=eq.завтрак
       ↓
    PostgREST → Postgres → JSONB → HTTP response → Python dict

  We use the service_role key to bypass RLS (Row Level Security),
  because this is a server-side bot, not a client-side app.

Recipe queries are inspired by the reference project (keto_project/src/lib/recipes.ts)
which uses RPC functions for ingredient exclusion filtering.
"""

from __future__ import annotations

from supabase import create_client, Client
import structlog

from src.config import settings
from src.models import Recipe, RecipeIngredient, RecipeMacros, UserProfile

logger = structlog.get_logger(__name__)

# Singleton Supabase client
_client: Client | None = None


def get_client() -> Client:
    """Get or create Supabase client with service_role key."""
    global _client
    if _client is None:
        _client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
        logger.info("supabase_client_created", url=settings.supabase_url)
    return _client


# ─── Recipe Queries ──────────────────────────────────────────────────────

CARD_FIELDS = "id, title, description, image_url, cooking_time, cooking_time_text, servings, category, macros, tags, ingredients, instructions"


def get_recipes(
    category: str | None = None,
    excluded: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
) -> list[Recipe]:
    """
    Fetch recipes with optional category and ingredient exclusion.

    When excluded is non-empty, uses the get_filtered_recipes RPC function
    (server-side ILIKE on JSONB ingredient names — see reference project).
    When excluded is empty, uses direct PostgREST query (cacheable).
    """
    client = get_client()
    excluded = excluded or []

    if not excluded:
        # Fast path: direct select
        query = (
            client.table("recipes")
            .select(CARD_FIELDS)
            .eq("is_published", True)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if category:
            query = query.eq("category", category)

        response = query.execute()
        return [_parse_recipe(r) for r in (response.data or [])]
    else:
        # Filtered path: server-side ingredient exclusion via RPC
        response = client.rpc(
            "get_filtered_recipes",
            {
                "p_category": category,
                "p_excluded": excluded,
                "p_offset": offset,
                "p_limit": limit,
            },
        ).execute()
        return [_parse_recipe(r) for r in (response.data or [])]


def search_recipes(
    query: str,
    excluded: list[str] | None = None,
    limit: int = 10,
) -> list[Recipe]:
    """
    Search recipes by title text.
    Uses FTS (full-text search) for queries >= 3 chars, ILIKE for shorter.
    """
    client = get_client()
    excluded = excluded or []
    trimmed = query.strip()
    if not trimmed:
        return []

    if not excluded:
        q = (
            client.table("recipes")
            .select(CARD_FIELDS)
            .eq("is_published", True)
        )
        if len(trimmed) >= 3:
            # FTS via textSearch — uses GIN index idx_recipes_fts
            q = q.text_search("title", trimmed, config="russian")
        else:
            q = q.ilike("title", f"%{trimmed}%")

        response = q.order("created_at", desc=True).limit(limit).execute()
        return [_parse_recipe(r) for r in (response.data or [])]
    else:
        # Use search_filtered_recipes RPC
        response = client.rpc(
            "search_filtered_recipes",
            {
                "p_query": trimmed,
                "p_excluded": excluded,
                "p_limit": limit,
            },
        ).execute()
        return [_parse_recipe(r) for r in (response.data or [])]


def get_recipe_by_id(recipe_id: str) -> Recipe | None:
    """Fetch a single recipe by UUID."""
    client = get_client()
    response = (
        client.table("recipes")
        .select("*")
        .eq("id", recipe_id)
        .eq("is_published", True)
        .maybe_single()
        .execute()
    )
    if response.data:
        return _parse_recipe(response.data)
    return None


# ─── User Profile Queries ────────────────────────────────────────────────

def get_user_by_telegram_id(tg_id: int) -> UserProfile | None:
    """Find user by telegram_id."""
    client = get_client()
    response = (
        client.table("users")
        .select("*")
        .eq("telegram_id", tg_id)
        .maybe_single()
        .execute()
    )
    if response.data:
        return _parse_user(response.data)
    return None


def create_user(
    tg_id: int,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
    language_code: str = "ru",
) -> UserProfile:
    """Create a new user record from Telegram data."""
    client = get_client()
    response = (
        client.table("users")
        .insert(
            {
                "telegram_id": tg_id,
                "telegram_first_name": first_name,
                "telegram_last_name": last_name,
                "telegram_username": username,
                "language_code": language_code,
            }
        )
        .execute()
    )
    return _parse_user(response.data[0])


def update_user_profile(user_id: str, patch: dict) -> None:
    """Apply a validated profile patch to the users table."""
    client = get_client()
    # Only allow known fields
    allowed = {
        "taste_preferences", "diabetes_type", "lactose_intolerant",
        "allergies_detail", "dietary_restrictions", "weight_kg",
        "target_weight_kg", "bot_onboarding_completed",
        "telegram_first_name", "telegram_last_name", "telegram_username",
    }
    safe_patch = {k: v for k, v in patch.items() if k in allowed and v is not None}
    if not safe_patch:
        return

    safe_patch["updated_at"] = "now()"
    client.table("users").update(safe_patch).eq("id", user_id).execute()
    logger.info("user_profile_updated", user_id=user_id, fields=list(safe_patch.keys()))


# ─── Parsing helpers ─────────────────────────────────────────────────────

def _parse_recipe(data: dict) -> Recipe:
    """Convert raw Supabase response to Recipe model."""
    ingredients_raw = data.get("ingredients", [])
    ingredients = []
    if isinstance(ingredients_raw, list):
        for ing in ingredients_raw:
            if isinstance(ing, dict):
                ingredients.append(
                    RecipeIngredient(
                        name=ing.get("name", ""),
                        amount=str(ing.get("amount", "")),
                        unit=str(ing.get("unit", "")),
                    )
                )

    macros_raw = data.get("macros", {})
    macros = RecipeMacros(
        calories=macros_raw.get("calories", 0),
        protein=macros_raw.get("protein", 0),
        fat=macros_raw.get("fat", 0),
        carbs=macros_raw.get("carbs", 0),
        fiber=macros_raw.get("fiber", 0),
    )

    return Recipe(
        id=str(data["id"]),
        title=data.get("title", ""),
        description=data.get("description"),
        image_url=data.get("image_url"),
        cooking_time=data.get("cooking_time"),
        cooking_time_text=data.get("cooking_time_text"),
        servings=data.get("servings", 2),
        category=data.get("category", "обед"),
        ingredients=ingredients,
        instructions=data.get("instructions", []),
        macros=macros,
        tags=data.get("tags") or [],
    )


def _parse_user(data: dict) -> UserProfile:
    """Convert raw Supabase response to UserProfile model."""
    return UserProfile(
        id=str(data["id"]),
        telegram_id=data["telegram_id"],
        telegram_username=data.get("telegram_username"),
        telegram_first_name=data.get("telegram_first_name"),
        language_code=data.get("language_code", "ru"),
        weight_kg=data.get("weight_kg"),
        target_weight_kg=data.get("target_weight_kg"),
        height_cm=data.get("height_cm"),
        birth_date=str(data["birth_date"]) if data.get("birth_date") else None,
        gender=data.get("gender"),
        activity_level=data.get("activity_level", "moderate"),
        health_goals=data.get("health_goals") or [],
        dietary_restrictions=data.get("dietary_restrictions") or [],
        diabetes_type=data.get("diabetes_type"),
        lactose_intolerant=data.get("lactose_intolerant", False),
        allergies_detail=data.get("allergies_detail") or [],
        taste_preferences=data.get("taste_preferences") or [],
        bot_onboarding_completed=data.get("bot_onboarding_completed", False),
        is_blocked=data.get("is_blocked", False),
        created_at=str(data["created_at"]) if data.get("created_at") else None,
    )
