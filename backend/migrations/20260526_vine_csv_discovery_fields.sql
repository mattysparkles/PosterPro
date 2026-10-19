ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS brand VARCHAR(255);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS category VARCHAR(255);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS source_status VARCHAR(64);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS review_deadline DATE;
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS item_url TEXT;
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS manual_amazon_url TEXT;
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS amazon_match_status VARCHAR(64);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS amazon_match_confidence VARCHAR(32);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS amazon_match_asin VARCHAR(16);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS amazon_match_title VARCHAR(512);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS amazon_source_page_url TEXT;
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS image_import_status VARCHAR(64);
ALTER TABLE vine_import_items ADD COLUMN IF NOT EXISTS image_import_error TEXT;

CREATE INDEX IF NOT EXISTS idx_vine_import_items_amazon_match_status ON vine_import_items(amazon_match_status);
