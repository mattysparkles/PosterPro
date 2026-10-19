ALTER TYPE marketplacename ADD VALUE IF NOT EXISTS 'offerup';

CREATE TABLE IF NOT EXISTS marketplace_extension_devices (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  device_key VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL DEFAULT 'PosterPro browser',
  browser VARCHAR(64),
  extension_version VARCHAR(32),
  token_hash VARCHAR(64) NOT NULL UNIQUE,
  last_seen_at TIMESTAMP,
  last_claim_at TIMESTAMP,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_devices_user_id ON marketplace_extension_devices(user_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_devices_last_seen_at ON marketplace_extension_devices(last_seen_at);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_devices_revoked_at ON marketplace_extension_devices(revoked_at);

CREATE TABLE IF NOT EXISTS marketplace_extension_pairing_codes (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  code_hash VARCHAR(64) NOT NULL UNIQUE,
  expires_at TIMESTAMP NOT NULL,
  consumed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_pairing_codes_user_id ON marketplace_extension_pairing_codes(user_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_pairing_codes_expires_at ON marketplace_extension_pairing_codes(expires_at);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_pairing_codes_consumed_at ON marketplace_extension_pairing_codes(consumed_at);

CREATE TABLE IF NOT EXISTS marketplace_extension_jobs (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  crosspost_job_id INTEGER REFERENCES marketplace_crosspost_jobs(id),
  marketplace VARCHAR(32) NOT NULL,
  action VARCHAR(16) NOT NULL DEFAULT 'CREATE',
  status VARCHAR(40) NOT NULL DEFAULT 'QUEUED',
  priority INTEGER NOT NULL DEFAULT 1,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  payload_version INTEGER NOT NULL DEFAULT 1,
  payload_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  device_id INTEGER REFERENCES marketplace_extension_devices(id),
  lease_expires_at TIMESTAMP,
  claimed_at TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  last_state_at TIMESTAMP,
  external_listing_id VARCHAR(255),
  external_url TEXT,
  error_code VARCHAR(100),
  error_detail TEXT,
  result JSONB,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_user_id ON marketplace_extension_jobs(user_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_listing_id ON marketplace_extension_jobs(listing_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_crosspost_job_id ON marketplace_extension_jobs(crosspost_job_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_marketplace ON marketplace_extension_jobs(marketplace);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_status ON marketplace_extension_jobs(status);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_priority ON marketplace_extension_jobs(priority);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_device_id ON marketplace_extension_jobs(device_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_extension_jobs_lease_expires_at ON marketplace_extension_jobs(lease_expires_at);
