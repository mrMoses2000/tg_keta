-- ============================================================
-- Migration 005: Processed Updates / Idempotency (local Postgres)
-- Prevents duplicate processing of Telegram update_id.
-- ============================================================

CREATE TABLE IF NOT EXISTS processed_updates (
    telegram_update_id BIGINT PRIMARY KEY,
    status             TEXT NOT NULL DEFAULT 'received',
    -- 'received' | 'processing' | 'completed' | 'failed'
    worker_id          TEXT,
    created_at         TIMESTAMPTZ DEFAULT now(),
    completed_at       TIMESTAMPTZ
);
