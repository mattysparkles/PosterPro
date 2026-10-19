CREATE TABLE IF NOT EXISTS storage_unit_batches (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    storage_unit_name VARCHAR(255),
    status VARCHAR(64) NOT NULL DEFAULT 'INGESTED',
    overnight_mode BOOLEAN NOT NULL DEFAULT FALSE,
    total_items INTEGER NOT NULL DEFAULT 0,
    processed_items INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    pipeline_task_id VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS batch_id INTEGER REFERENCES storage_unit_batches(id);

CREATE INDEX IF NOT EXISTS ix_storage_unit_batches_user_id ON storage_unit_batches(user_id);
CREATE INDEX IF NOT EXISTS ix_storage_unit_batches_status ON storage_unit_batches(status);
CREATE INDEX IF NOT EXISTS ix_listings_batch_id ON listings(batch_id);
