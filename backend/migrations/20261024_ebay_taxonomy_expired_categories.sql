CREATE TABLE IF NOT EXISTS marketplace_taxonomy_expired_categories (
  marketplace_id VARCHAR(32) NOT NULL,
  category_tree_id VARCHAR(32) NOT NULL,
  current_tree_version VARCHAR(120) NOT NULL,
  expired_category_id VARCHAR(160) NOT NULL,
  replacement_category_id VARCHAR(160),
  checked_at TIMESTAMP NOT NULL,
  source VARCHAR(120) NOT NULL,
  PRIMARY KEY (marketplace_id, category_tree_id, current_tree_version, expired_category_id)
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_expired_replacement
  ON marketplace_taxonomy_expired_categories(marketplace_id, replacement_category_id);
