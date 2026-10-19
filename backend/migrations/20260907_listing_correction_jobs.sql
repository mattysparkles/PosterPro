CREATE TABLE IF NOT EXISTS listing_correction_jobs (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), listing_id INTEGER NOT NULL REFERENCES listings(id), requested_by INTEGER NOT NULL REFERENCES users(id),
 priority INTEGER NOT NULL DEFAULT 0, fields JSONB NOT NULL DEFAULT '[]', operator_note TEXT, source VARCHAR(64) NOT NULL DEFAULT 'MANUAL_CORRECTION', status VARCHAR(64) NOT NULL DEFAULT 'queued', attempt_count INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMP NULL, claimed_at TIMESTAMP NULL, started_at TIMESTAMP NULL, completed_at TIMESTAMP NULL, failure_reason TEXT, before_snapshot JSONB, after_snapshot JSONB, material_delta JSONB, result JSONB, superseded_by INTEGER NULL, created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_listing_correction_jobs_queue ON listing_correction_jobs (status, priority, created_at);
