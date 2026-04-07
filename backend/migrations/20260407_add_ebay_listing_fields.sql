-- Adds eBay publishing state to listings table.
ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS ebay_listing_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS ebay_publish_status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    ADD COLUMN IF NOT EXISTS marketplace_data JSON;

CREATE INDEX IF NOT EXISTS ix_listings_ebay_listing_id ON listings (ebay_listing_id);
CREATE INDEX IF NOT EXISTS ix_listings_ebay_publish_status ON listings (ebay_publish_status);
