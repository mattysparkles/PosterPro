CREATE TABLE IF NOT EXISTS marketplace_publish_attempts (
    id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    marketplace VARCHAR(64) NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NULL,
    finished_at TIMESTAMP WITHOUT TIME ZONE NULL,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    preflight_status VARCHAR(64) NULL,
    payload_snapshot JSON NULL,
    payload_hash VARCHAR(255) NULL,
    inventory_item_sku VARCHAR(255) NULL,
    offer_id VARCHAR(255) NULL,
    marketplace_listing_id VARCHAR(255) NULL,
    marketplace_status VARCHAR(64) NULL,
    translated_error JSON NULL,
    raw_error TEXT NULL,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    retry_count INTEGER NOT NULL DEFAULT 0,
    previous_attempt_id INTEGER NULL REFERENCES marketplace_publish_attempts(id) ON DELETE SET NULL,
    job_id INTEGER NULL REFERENCES marketplace_crosspost_jobs(id) ON DELETE SET NULL,
    task_id VARCHAR(255) NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_listing_id ON marketplace_publish_attempts(listing_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_user_id ON marketplace_publish_attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_marketplace ON marketplace_publish_attempts(marketplace);
CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_started_at ON marketplace_publish_attempts(started_at);
CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_marketplace_listing_id ON marketplace_publish_attempts(marketplace_listing_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_offer_id ON marketplace_publish_attempts(offer_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_publish_attempts_task_id ON marketplace_publish_attempts(task_id);
