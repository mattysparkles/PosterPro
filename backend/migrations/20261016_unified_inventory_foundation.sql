-- Additive foundation for physical inventory. Existing inventory_bins and
-- slate assignments remain authoritative for legacy tote placement.
ALTER TABLE inventory_bins ADD COLUMN IF NOT EXISTS container_uuid VARCHAR(64);
ALTER TABLE inventory_bins ADD COLUMN IF NOT EXISTS parent_location_key VARCHAR(128);
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_bins_container_uuid ON inventory_bins(container_uuid) WHERE container_uuid IS NOT NULL;

CREATE TABLE IF NOT EXISTS inventory_products (
  id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), product_key VARCHAR(64) NOT NULL,
  title VARCHAR(255), identifiers_json JSON, facts_json JSON, source_json JSON,
  enrichment_status VARCHAR(32) NOT NULL DEFAULT 'provisional', created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_products_user_key UNIQUE(user_id, product_key)
);
CREATE TABLE IF NOT EXISTS inventory_stock (
  id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), product_id INTEGER NOT NULL REFERENCES inventory_products(id), location_key VARCHAR(128) NOT NULL,
  on_hand INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0, sold INTEGER NOT NULL DEFAULT 0, damaged INTEGER NOT NULL DEFAULT 0, missing INTEGER NOT NULL DEFAULT 0, shipped INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_stock_location UNIQUE(user_id, product_id, location_key)
);
CREATE TABLE IF NOT EXISTS inventory_packages (
  id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), package_uuid VARCHAR(64) NOT NULL UNIQUE, package_code VARCHAR(64) NOT NULL, parent_location_key VARCHAR(128), status VARCHAR(32) NOT NULL DEFAULT 'open', metadata_json JSON,
  created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(), CONSTRAINT uq_inventory_packages_user_code UNIQUE(user_id, package_code)
);
CREATE TABLE IF NOT EXISTS inventory_events (
  id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), operation_id VARCHAR(64) NOT NULL, event_type VARCHAR(64) NOT NULL, entity_type VARCHAR(64) NOT NULL, entity_key VARCHAR(128) NOT NULL, session_id VARCHAR(255), payload_json JSON, created_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_events_operation UNIQUE(user_id, operation_id)
);
CREATE INDEX IF NOT EXISTS ix_inventory_stock_user_product ON inventory_stock(user_id, product_id);
CREATE INDEX IF NOT EXISTS ix_inventory_events_entity ON inventory_events(user_id, entity_type, entity_key, created_at);
ALTER TABLE inventory_stock ADD CONSTRAINT ck_inventory_stock_nonnegative CHECK (on_hand >= 0 AND reserved >= 0 AND sold >= 0 AND damaged >= 0 AND missing >= 0 AND shipped >= 0) NOT VALID;
CREATE TABLE IF NOT EXISTS inventory_manifest_imports (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), tote_id INTEGER NOT NULL REFERENCES inventory_bins(id), filename VARCHAR(255) NOT NULL, checksum VARCHAR(64) NOT NULL, duplicate_policy VARCHAR(32) NOT NULL, raw_scan_count INTEGER NOT NULL, source_text TEXT NOT NULL, operation_id VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'applied', created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(), CONSTRAINT uq_inventory_manifest_policy UNIQUE(user_id, checksum, duplicate_policy)
);
CREATE TABLE IF NOT EXISTS inventory_manifest_entries (
 id SERIAL PRIMARY KEY, manifest_import_id INTEGER NOT NULL REFERENCES inventory_manifest_imports(id), barcode VARCHAR(255) NOT NULL, scan_count INTEGER NOT NULL, applied_quantity INTEGER NOT NULL, product_id INTEGER REFERENCES inventory_products(id), resolution VARCHAR(32) NOT NULL DEFAULT 'unknown', created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(), CONSTRAINT uq_inventory_manifest_entry UNIQUE(manifest_import_id, barcode)
);
