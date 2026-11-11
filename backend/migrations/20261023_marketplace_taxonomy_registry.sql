-- Additive, versioned cross-marketplace taxonomy registry. Existing eBay and
-- Mercari tables remain authoritative compatibility sources during migration.
CREATE TABLE IF NOT EXISTS marketplace_taxonomy_versions (
  version_id VARCHAR(200) PRIMARY KEY,
  marketplace VARCHAR(32) NOT NULL,
  scope_key VARCHAR(100) NOT NULL DEFAULT '',
  native_version VARCHAR(120),
  source VARCHAR(160) NOT NULL,
  source_url TEXT,
  source_evidence_json TEXT NOT NULL DEFAULT '{}',
  captured_at TIMESTAMP NOT NULL,
  last_checked_at TIMESTAMP,
  verification_status VARCHAR(40) NOT NULL DEFAULT 'unknown',
  completeness_status VARCHAR(24) NOT NULL DEFAULT 'partial',
  staleness_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
  category_count INTEGER NOT NULL DEFAULT 0,
  terminal_count INTEGER NOT NULL DEFAULT 0,
  unresolved_count INTEGER NOT NULL DEFAULT 0,
  is_current BOOLEAN NOT NULL DEFAULT FALSE,
  notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_marketplace_taxonomy_versions_latest
  ON marketplace_taxonomy_versions(marketplace, scope_key, is_current, captured_at);

CREATE TABLE IF NOT EXISTS marketplace_taxonomy_categories (
  version_id VARCHAR(200) NOT NULL,
  marketplace VARCHAR(32) NOT NULL,
  category_key VARCHAR(220) NOT NULL,
  category_id VARCHAR(160),
  category_name TEXT NOT NULL,
  parent_category_key VARCHAR(220),
  parent_category_id VARCHAR(160),
  full_path TEXT NOT NULL,
  path_json TEXT NOT NULL,
  depth INTEGER NOT NULL,
  terminal_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
  publishability_status VARCHAR(24) NOT NULL DEFAULT 'unknown',
  required_attributes_json TEXT,
  optional_attributes_json TEXT,
  recommended_attributes_json TEXT,
  conditional_attributes_json TEXT,
  data_source VARCHAR(160) NOT NULL,
  source_evidence_json TEXT NOT NULL DEFAULT '{}',
  last_verified_at TIMESTAMP,
  verification_status VARCHAR(40) NOT NULL DEFAULT 'unknown',
  staleness_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
  automated_selection_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(version_id, category_key)
);
CREATE INDEX IF NOT EXISTS ix_marketplace_taxonomy_categories_path
  ON marketplace_taxonomy_categories(marketplace, full_path);
CREATE INDEX IF NOT EXISTS ix_marketplace_taxonomy_categories_leaf
  ON marketplace_taxonomy_categories(marketplace, version_id, terminal_status, automated_selection_eligible);
