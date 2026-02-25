-- ============================================================
-- Migration 006: Recipe Tags enrichment (Supabase, applied via MCP)
-- For batch tagify: taste, allergens, dietary flags per recipe.
-- ============================================================

CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id    UUID PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
    taste_sweet  BOOLEAN DEFAULT FALSE,
    taste_salty  BOOLEAN DEFAULT FALSE,
    taste_spicy  BOOLEAN DEFAULT FALSE,
    taste_sour   BOOLEAN DEFAULT FALSE,
    meal_type    TEXT,
    flags        JSONB DEFAULT '{}',
    allergens    TEXT[] DEFAULT '{}',
    tag_source   TEXT DEFAULT 'auto',
    tag_version  INT DEFAULT 1,
    updated_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recipe_tags_allergens
    ON recipe_tags USING GIN(allergens);
CREATE INDEX IF NOT EXISTS idx_recipe_tags_flags
    ON recipe_tags USING GIN(flags);
