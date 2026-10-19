ALTER TABLE users
    ADD COLUMN IF NOT EXISTS sale_detection_platforms JSON;

CREATE TABLE IF NOT EXISTS sales (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    listing_id INTEGER NULL REFERENCES listings(id),
    platform VARCHAR(32) NOT NULL,
    marketplace_order_id VARCHAR(255),
    marketplace_listing_id VARCHAR(255),
    quantity INTEGER NOT NULL DEFAULT 1,
    amount DOUBLE PRECISION,
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    sold_at TIMESTAMP,
    status VARCHAR(32) NOT NULL DEFAULT 'DETECTED',
    details JSON,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id);
CREATE INDEX IF NOT EXISTS idx_sales_listing_id ON sales(listing_id);
CREATE INDEX IF NOT EXISTS idx_sales_platform ON sales(platform);
CREATE INDEX IF NOT EXISTS idx_sales_order_id ON sales(marketplace_order_id);
CREATE INDEX IF NOT EXISTS idx_sales_marketplace_listing_id ON sales(marketplace_listing_id);
