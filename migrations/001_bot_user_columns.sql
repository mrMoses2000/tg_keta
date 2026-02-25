-- ============================================================
-- Migration 001: Add bot-specific columns to users table (additive)
-- Safe: only adds new columns, does not modify existing ones.
-- ============================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS diabetes_type TEXT;
-- NULL | 'type1' | 'type2' | 'gestational'

ALTER TABLE users ADD COLUMN IF NOT EXISTS lactose_intolerant BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS allergies_detail JSONB DEFAULT '[]';
-- [{allergen: "dairy", severity: "intolerance"}, ...]

ALTER TABLE users ADD COLUMN IF NOT EXISTS taste_preferences TEXT[] DEFAULT '{}';
-- ['sweet', 'salty', 'spicy', 'sour']

ALTER TABLE users ADD COLUMN IF NOT EXISTS bot_onboarding_completed BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN IF NOT EXISTS bot_language TEXT DEFAULT 'ru';
