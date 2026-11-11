-- Additive, tenant-scoped resumable taxonomy collection state.
-- Category and version evidence remains in the existing taxonomy registry.
CREATE TABLE IF NOT EXISTS marketplace_taxonomy_traversal_checkpoints (
  owner_user_id INTEGER NOT NULL,
  marketplace VARCHAR(24) NOT NULL,
  source_key VARCHAR(120) NOT NULL,
  source_version VARCHAR(160),
  checkpoint_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL DEFAULT 'in_progress',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(owner_user_id, marketplace, source_key)
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_traversal_checkpoint_updated
  ON marketplace_taxonomy_traversal_checkpoints(marketplace, updated_at);
