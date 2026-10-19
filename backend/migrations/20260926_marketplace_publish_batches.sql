CREATE TABLE IF NOT EXISTS marketplace_publish_batches (
    id VARCHAR(64) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    operation VARCHAR(64) NOT NULL DEFAULT 'bulk_publish',
    selected_count INTEGER NOT NULL DEFAULT 0,
    selected_listing_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    requested_marketplaces JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'preflighting',
    summary JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_marketplace_publish_batches_user_id ON marketplace_publish_batches(user_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_publish_batches_status ON marketplace_publish_batches(status);
ALTER TABLE marketplace_crosspost_jobs ADD COLUMN IF NOT EXISTS batch_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_marketplace_crosspost_jobs_batch_id ON marketplace_crosspost_jobs(batch_id);
