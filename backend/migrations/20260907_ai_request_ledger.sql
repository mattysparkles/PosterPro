CREATE TABLE IF NOT EXISTS ai_request_ledger (
 id SERIAL PRIMARY KEY, user_id INTEGER NULL REFERENCES users(id), listing_id INTEGER NULL REFERENCES listings(id),
 purpose VARCHAR(64) NOT NULL, provider VARCHAR(32), model VARCHAR(96), endpoint VARCHAR(128), input_signature VARCHAR(128) NOT NULL,
 attempt_number INTEGER NOT NULL DEFAULT 1, status VARCHAR(32) NOT NULL DEFAULT 'started', image_count INTEGER NOT NULL DEFAULT 0,
 before_quality JSONB, after_quality JSONB, changed_fields JSONB, no_progress_count INTEGER NOT NULL DEFAULT 0,
 tokens_input INTEGER, tokens_output INTEGER, latency_ms INTEGER, error TEXT,
 created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ai_request_ledger_signature ON ai_request_ledger(input_signature);
CREATE INDEX IF NOT EXISTS ix_ai_request_ledger_listing ON ai_request_ledger(listing_id);
CREATE INDEX IF NOT EXISTS ix_ai_request_ledger_purpose ON ai_request_ledger(purpose);
