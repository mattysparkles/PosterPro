-- Inventory management fields for quantity sync, labels, and stale tracking.
ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS platform_quantities JSON,
    ADD COLUMN IF NOT EXISTS custom_labels JSON,
    ADD COLUMN IF NOT EXISTS last_refreshed TIMESTAMP WITHOUT TIME ZONE;

UPDATE listings
SET quantity = 1
WHERE quantity IS NULL;

ALTER TABLE listings
    ALTER COLUMN quantity SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_listings_last_refreshed ON listings (last_refreshed);
