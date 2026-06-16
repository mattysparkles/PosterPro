CREATE TABLE IF NOT EXISTS marketplace_metadata_cache (
    id SERIAL PRIMARY KEY,
    marketplace VARCHAR(64) NOT NULL,
    cache_key VARCHAR(255) NOT NULL,
    payload JSON,
    source_version VARCHAR(64),
    expires_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_marketplace_metadata_cache_marketplace ON marketplace_metadata_cache (marketplace);
CREATE INDEX IF NOT EXISTS ix_marketplace_metadata_cache_cache_key ON marketplace_metadata_cache (cache_key);
CREATE INDEX IF NOT EXISTS ix_marketplace_metadata_cache_expires_at ON marketplace_metadata_cache (expires_at);
