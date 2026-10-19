ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'public';
UPDATE users
SET role = 'owner'
WHERE COALESCE(is_admin, FALSE) = TRUE
  AND (role IS NULL OR role = '' OR role = 'public');
UPDATE users
SET role = 'public'
WHERE COALESCE(is_admin, FALSE) = FALSE
  AND (role IS NULL OR role = '');

ALTER TABLE listings ADD COLUMN IF NOT EXISTS source_type VARCHAR(64);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS source_metadata JSON;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS restricted_review_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS restricted_reasons JSON;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS detected_category_guess VARCHAR(255);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS marketplace_allowed_status VARCHAR(64);

CREATE TABLE IF NOT EXISTS vine_import_batches (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    filename VARCHAR(255) NOT NULL,
    source_type VARCHAR(16) NOT NULL,
    report_year INTEGER,
    status VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    parsed_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    locked_count INTEGER NOT NULL DEFAULT 0,
    cancelled_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    drafts_created_count INTEGER NOT NULL DEFAULT 0,
    stats_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vine_import_items (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES vine_import_batches(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    order_number VARCHAR(64),
    asin VARCHAR(16),
    product_name VARCHAR(512),
    order_type VARCHAR(32),
    order_date DATE,
    shipped_date DATE,
    cancelled_date DATE,
    estimated_tax_value DOUBLE PRECISION,
    eligible_after DATE,
    eligibility_status VARCHAR(64) NOT NULL DEFAULT 'invalid',
    raw_row_json JSON,
    parse_warnings_json JSON,
    media_status VARCHAR(64),
    media_asset_ids_json JSON,
    restricted_review_required BOOLEAN NOT NULL DEFAULT FALSE,
    restricted_reasons JSON,
    detected_category_guess VARCHAR(255),
    marketplace_allowed_status VARCHAR(64),
    -- PosterPro currently represents inventory records and listing drafts through the listings table.
    inventory_item_id INTEGER REFERENCES listings(id),
    listing_id INTEGER REFERENCES listings(id),
    source_confidence VARCHAR(16) NOT NULL DEFAULT 'high',
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vine_import_batches_user_id ON vine_import_batches(user_id);
CREATE INDEX IF NOT EXISTS idx_vine_import_items_batch_id ON vine_import_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_vine_import_items_asin ON vine_import_items(asin);
CREATE INDEX IF NOT EXISTS idx_vine_import_items_order_number ON vine_import_items(order_number);

CREATE TABLE IF NOT EXISTS product_media_cache (
    id SERIAL PRIMARY KEY,
    asin VARCHAR(16) NOT NULL,
    marketplace_region VARCHAR(8) NOT NULL DEFAULT 'US',
    product_url TEXT,
    title VARCHAR(255),
    primary_image_url TEXT,
    gallery_image_urls_json JSON,
    local_asset_ids_json JSON,
    source_provider VARCHAR(64),
    fetch_status VARCHAR(64),
    fetch_error TEXT,
    fetched_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_media_cache_asin_region ON product_media_cache(asin, marketplace_region);
