-- ============================================================
-- Migration 004: Outbound Events / Outbox (local Postgres)
-- Ensures reliable delivery of bot responses to Telegram.
-- ============================================================

CREATE TABLE IF NOT EXISTS outbound_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_chat_id BIGINT NOT NULL,
    reply_text       TEXT NOT NULL,
    reply_markup     JSONB,
    status           TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'sent' | 'failed'
    attempts         INT DEFAULT 0,
    last_attempt_at  TIMESTAMPTZ,
    error_message    TEXT,
    inbound_event_id UUID,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outbound_status
    ON outbound_events(status)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_outbound_chat_id
    ON outbound_events(telegram_chat_id);
CREATE INDEX IF NOT EXISTS idx_outbound_created_at
    ON outbound_events(created_at DESC);
