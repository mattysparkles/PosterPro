CREATE TABLE IF NOT EXISTS ai_request_reservations (
 id SERIAL PRIMARY KEY, input_signature VARCHAR(128) NOT NULL UNIQUE, pool VARCHAR(16) NOT NULL,
 budget_date DATE NOT NULL, reserved_tokens INTEGER NOT NULL, actual_tokens INTEGER,
 state VARCHAR(24) NOT NULL DEFAULT 'RESERVED', listing_id INTEGER REFERENCES listings(id), user_id INTEGER REFERENCES users(id),
 purpose VARCHAR(64) NOT NULL, next_attempt_at TIMESTAMP, error TEXT,
 created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ai_reservations_state ON ai_request_reservations(state);
CREATE INDEX IF NOT EXISTS ix_ai_reservations_budget ON ai_request_reservations(budget_date, pool);
CREATE TABLE IF NOT EXISTS ai_provider_circuit (
 id SMALLINT PRIMARY KEY DEFAULT 1, state VARCHAR(16) NOT NULL DEFAULT 'CLOSED', reason TEXT,
 opened_at TIMESTAMP, retry_after TIMESTAMP, last_error TEXT, updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE ai_provider_circuit ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE ai_request_reservations ADD COLUMN IF NOT EXISTS result_json JSONB;
ALTER TABLE ai_request_reservations ALTER COLUMN state TYPE VARCHAR(48);
INSERT INTO ai_provider_circuit (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
