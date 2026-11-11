CREATE TABLE IF NOT EXISTS canonical_product_concepts (
  concept_id VARCHAR(80) PRIMARY KEY,
  owner_user_id INTEGER NOT NULL,
  normalized_name VARCHAR(220) NOT NULL,
  concept_name TEXT NOT NULL,
  concept_description TEXT,
  structured_facts_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  classification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  created_by INTEGER NOT NULL,
  updated_by INTEGER NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(owner_user_id, normalized_name)
);
CREATE INDEX IF NOT EXISTS ix_canonical_product_concepts_owner
  ON canonical_product_concepts(owner_user_id, normalized_name);

CREATE TABLE IF NOT EXISTS marketplace_taxonomy_mappings (
  mapping_id VARCHAR(100) PRIMARY KEY,
  owner_user_id INTEGER NOT NULL,
  concept_id VARCHAR(80) NOT NULL,
  marketplace VARCHAR(32) NOT NULL,
  taxonomy_version_id VARCHAR(200) NOT NULL,
  taxonomy_native_version VARCHAR(120),
  category_key VARCHAR(220) NOT NULL,
  category_id VARCHAR(160),
  category_path TEXT NOT NULL,
  mapping_rationale TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  verification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  operator_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
  confirmed_by INTEGER,
  confirmed_at TIMESTAMP,
  last_validated_at TIMESTAMP,
  invalidated_at TIMESTAMP,
  invalidation_reason TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_marketplace_taxonomy_mappings_concept
  ON marketplace_taxonomy_mappings(owner_user_id, concept_id, marketplace, verification_status);
