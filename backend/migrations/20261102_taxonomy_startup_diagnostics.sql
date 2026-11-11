-- Durable browser taxonomy startup errors are separate from traversal
-- checkpoints so diagnostics cannot advance the collector's CAS revision.
CREATE TABLE IF NOT EXISTS marketplace_taxonomy_startup_diagnostics (
  diagnostic_id VARCHAR(64) PRIMARY KEY,
  owner_user_id INTEGER NOT NULL,
  device_id INTEGER NOT NULL,
  marketplace VARCHAR(24) NOT NULL,
  diagnostic_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_startup_diagnostics_owner_created
  ON marketplace_taxonomy_startup_diagnostics(owner_user_id, marketplace, created_at);
