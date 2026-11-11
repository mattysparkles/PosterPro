ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS listing_images JSON;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS condition_data JSON;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS shipping_profile JSON;
