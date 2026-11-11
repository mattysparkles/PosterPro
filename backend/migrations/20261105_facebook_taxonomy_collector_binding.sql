-- Additive, durable binding for the isolated Facebook seller-form collector.
-- Existing taxonomy observations and traversal checkpoints are untouched.
CREATE TABLE IF NOT EXISTS marketplace_facebook_taxonomy_collector_bindings (
  device_id INTEGER PRIMARY KEY,
  owner_user_id INTEGER NOT NULL,
  authorized_session_id VARCHAR(64) NOT NULL,
  collection_session_id VARCHAR(80) NOT NULL,
  window_id INTEGER NOT NULL,
  tab_id INTEGER NOT NULL,
  extension_version VARCHAR(32) NOT NULL,
  status VARCHAR(24) NOT NULL,
  checkpoint_revision INTEGER NOT NULL DEFAULT 0,
  current_path_json TEXT NOT NULL DEFAULT '[]',
  categories_discovered INTEGER NOT NULL DEFAULT 0,
  verified_leaves INTEGER NOT NULL DEFAULT 0,
  complete_schemas INTEGER NOT NULL DEFAULT 0,
  last_successful_operation VARCHAR(80),
  last_error_code VARCHAR(100),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fb_taxonomy_binding_session
  ON marketplace_facebook_taxonomy_collector_bindings(collection_session_id);

-- Snapshot the exact browser target without altering the existing job table.
-- Historical jobs have no target row and are intentionally not claimable.
CREATE TABLE IF NOT EXISTS marketplace_taxonomy_test_job_targets (
  job_id VARCHAR(64) PRIMARY KEY,
  collection_session_id VARCHAR(80) NOT NULL,
  collector_window_id INTEGER NOT NULL,
  collector_tab_id INTEGER NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_test_job_target_binding
  ON marketplace_taxonomy_test_job_targets(collection_session_id,collector_window_id,collector_tab_id);
