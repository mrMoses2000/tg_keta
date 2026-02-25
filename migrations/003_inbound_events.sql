-- ============================================================
-- Migration 003: Inbound Events (local Postgres)
-- Audit log of all incoming Telegram updates.
-- ============================================================

CREATE TABLE IF NOT EXISTS inbound_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_update_id  BIGINT NOT NULL,
    telegram_chat_id    BIGINT NOT NULL,
    telegram_user_id    BIGINT NOT NULL,
    message_text        TEXT,
    raw_update          JSONB NOT NULL,
    received_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inbound_update_id
    ON inbound_events(telegram_update_id);
CREATE INDEX IF NOT EXISTS idx_inbound_chat_id
    ON inbound_events(telegram_chat_id);
CREATE INDEX IF NOT EXISTS idx_inbound_received_at
    ON inbound_events(received_at DESC);
