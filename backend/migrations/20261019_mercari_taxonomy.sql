CREATE TABLE IF NOT EXISTS mercari_taxonomy_versions (
  version_id VARCHAR(80) PRIMARY KEY,
  marketplace VARCHAR(32) NOT NULL DEFAULT 'mercari_us',
  source VARCHAR(120) NOT NULL,
  source_url TEXT,
  captured_at TIMESTAMP NOT NULL,
  verification_status VARCHAR(40) NOT NULL DEFAULT 'unverified',
  node_count INTEGER NOT NULL DEFAULT 0,
  leaf_count INTEGER NOT NULL DEFAULT 0,
  unresolved_branches INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_by INTEGER,
  source_completeness VARCHAR(16) NOT NULL DEFAULT 'partial',
  root_count INTEGER NOT NULL DEFAULT 0,
  max_depth INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mercari_taxonomy_categories (
  version_id VARCHAR(80) NOT NULL,
  source_namespace VARCHAR(80) NOT NULL,
  source_category_id VARCHAR(120),
  category_key VARCHAR(80) NOT NULL,
  category_name TEXT NOT NULL,
  category_url TEXT,
  parent_category_key VARCHAR(80),
  parent_source_category_id VARCHAR(120),
  full_path TEXT NOT NULL,
  path_json TEXT NOT NULL,
  depth INTEGER NOT NULL,
  is_leaf BOOLEAN NOT NULL,
  leaf_status VARCHAR(20) NOT NULL DEFAULT 'unknown',
  verification_status VARCHAR(40) NOT NULL DEFAULT 'unverified',
  verification_evidence_job_id INTEGER,
  path_verified_at TIMESTAMP,
  observed_at TIMESTAMP NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (version_id, category_key)
);
CREATE INDEX IF NOT EXISTS ix_mercari_taxonomy_name ON mercari_taxonomy_categories(category_name);
CREATE INDEX IF NOT EXISTS ix_mercari_taxonomy_path ON mercari_taxonomy_categories(full_path);
CREATE INDEX IF NOT EXISTS ix_mercari_taxonomy_leaf ON mercari_taxonomy_categories(version_id, is_leaf);
CREATE TABLE IF NOT EXISTS mercari_taxonomy_mappings (
  mapping_key VARCHAR(255) PRIMARY KEY,
  mapping_label TEXT NOT NULL,
  version_id VARCHAR(80) NOT NULL,
  category_key VARCHAR(80) NOT NULL,
  approved_by INTEGER,
  approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  rationale TEXT
);
