-- Additive shelf census support. Existing listing, SKU, marketplace, stock,
-- tote, and print-job rows are untouched.
CREATE TABLE IF NOT EXISTS inventory_locations (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  location_key VARCHAR(128) NOT NULL,
  unit_id VARCHAR(32) NOT NULL,
  section_code VARCHAR(32) NOT NULL,
  shelf_level VARCHAR(32) NOT NULL,
  position_code VARCHAR(32),
  location_type VARCHAR(32) NOT NULL DEFAULT 'shelf',
  accessibility VARCHAR(32) NOT NULL DEFAULT 'accessible',
  is_full BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_location_user_key UNIQUE(user_id, location_key)
);
CREATE INDEX IF NOT EXISTS ix_inventory_locations_user_unit ON inventory_locations(user_id, unit_id);
CREATE TABLE IF NOT EXISTS inventory_census_sessions (
  id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  session_key VARCHAR(64) NOT NULL, active_location_key VARCHAR(128), status VARCHAR(32) NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_census_session_user_key UNIQUE(user_id, session_key)
);
CREATE TABLE IF NOT EXISTS inventory_census_records (
  id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), item_uuid VARCHAR(64) NOT NULL,
  barcode_token VARCHAR(13) NOT NULL UNIQUE,
  operation_id VARCHAR(64) NOT NULL, request_fingerprint VARCHAR(64) NOT NULL, session_key VARCHAR(64) NOT NULL,
  listing_id INTEGER REFERENCES listings(id), inventory_product_id INTEGER REFERENCES inventory_products(id),
  location_key VARCHAR(128) NOT NULL, title_snapshot VARCHAR(255) NOT NULL, sku_snapshot VARCHAR(255),
  provisional BOOLEAN NOT NULL DEFAULT FALSE, status VARCHAR(32) NOT NULL DEFAULT 'present',
  created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_inventory_census_item_uuid UNIQUE(user_id, item_uuid),
  CONSTRAINT uq_inventory_census_operation UNIQUE(user_id, operation_id)
);
CREATE INDEX IF NOT EXISTS ix_inventory_census_location ON inventory_census_records(user_id, location_key, status);
CREATE INDEX IF NOT EXISTS ix_inventory_census_listing ON inventory_census_records(user_id, listing_id, status);

-- Agent credentials are private to inventory printing and cannot authorize
-- general PosterPro operations. Pairing codes and access tokens are stored as
-- one-way hashes; the agent receives its raw access token exactly once.
CREATE TABLE IF NOT EXISTS inventory_print_agents (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  display_name VARCHAR(128) NOT NULL,
  printer_name VARCHAR(128) NOT NULL DEFAULT 'Sparkles SKU Printer',
  printer_profile_json JSONB,
  token_hash VARCHAR(64) UNIQUE,
  pairing_code_hash VARCHAR(64),
  pairing_code_expires_at TIMESTAMP,
  paired_at TIMESTAMP,
  last_seen_at TIMESTAMP,
  last_status VARCHAR(32) NOT NULL DEFAULT 'offline',
  last_error TEXT,
  capabilities_json JSONB,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  hardware_tested BOOLEAN NOT NULL DEFAULT FALSE,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_inventory_print_agents_user ON inventory_print_agents(user_id);
CREATE INDEX IF NOT EXISTS ix_inventory_print_agents_pairing ON inventory_print_agents(pairing_code_hash);

-- Nullable additions keep older Slate/package print jobs compatible. The
-- partial unique index only governs explicit Census idempotency keys.
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS job_uuid VARCHAR(36);
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(160);
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS label_template_version INTEGER;
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS agent_id INTEGER REFERENCES inventory_print_agents(id);
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS delivery_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS lease_token VARCHAR(128);
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP;
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS spooler_job_id VARCHAR(128);
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS outcome_note TEXT;
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS reprint_of_job_id INTEGER REFERENCES inventory_print_jobs(id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_print_job_job_uuid ON inventory_print_jobs(job_uuid) WHERE job_uuid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_print_job_idempotency_key ON inventory_print_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_inventory_print_job_agent ON inventory_print_jobs(agent_id);
CREATE INDEX IF NOT EXISTS ix_inventory_print_job_lease ON inventory_print_jobs(lease_expires_at);
