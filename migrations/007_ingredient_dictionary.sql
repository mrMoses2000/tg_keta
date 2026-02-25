-- ============================================================
-- Migration 007: Ingredient Dictionary (Supabase, applied via MCP)
-- Maps ingredient names to allergen categories.
-- ============================================================

CREATE TABLE IF NOT EXISTS ingredient_dictionary (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    term            TEXT NOT NULL UNIQUE,
    allergen        TEXT,
    is_keto_friendly BOOLEAN DEFAULT TRUE,
    carbs_per_100g  DECIMAL(5,1),
    confidence      DECIMAL(3,2) DEFAULT 1.0,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Seed common allergen mappings
INSERT INTO ingredient_dictionary (term, allergen, is_keto_friendly) VALUES
    ('молоко', 'dairy', true),
    ('сливки', 'dairy', true),
    ('сметана', 'dairy', true),
    ('творог', 'dairy', true),
    ('сыр', 'dairy', true),
    ('масло сливочное', 'dairy', true),
    ('кефир', 'dairy', true),
    ('йогурт', 'dairy', true),
    ('яйцо', 'eggs', true),
    ('яйца', 'eggs', true),
    ('орех', 'nuts', true),
    ('миндаль', 'nuts', true),
    ('фундук', 'nuts', true),
    ('грецкий орех', 'nuts', true),
    ('кешью', 'nuts', true),
    ('арахис', 'peanuts', true),
    ('соя', 'soy', true),
    ('соевый соус', 'soy', true),
    ('пшеница', 'wheat', false),
    ('мука пшеничная', 'wheat', false),
    ('хлеб', 'wheat', false),
    ('свинина', NULL, true),
    ('бекон', NULL, true),
    ('лосось', 'fish', true),
    ('тунец', 'fish', true),
    ('сёмга', 'fish', true),
    ('треска', 'fish', true),
    ('креветки', 'shellfish', true),
    ('кальмар', 'shellfish', true),
    ('сахар', NULL, false),
    ('мёд', NULL, false),
    ('рис', NULL, false),
    ('картофель', NULL, false),
    ('банан', NULL, false)
ON CONFLICT (term) DO NOTHING;
