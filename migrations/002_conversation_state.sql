-- ============================================================
-- Migration 002: Conversation State (local Postgres)
-- FSM state per user for bot conversation tracking.
-- ============================================================

CREATE TABLE IF NOT EXISTS conversation_state (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    mode             TEXT NOT NULL DEFAULT 'idle',
    step             TEXT,
    context_summary  JSONB DEFAULT '{}',
    last_messages    JSONB DEFAULT '[]',
    updated_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_state_user_id
    ON conversation_state(user_id);
CREATE INDEX IF NOT EXISTS idx_conv_state_chat_id
    ON conversation_state(telegram_chat_id);
