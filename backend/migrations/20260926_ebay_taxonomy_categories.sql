CREATE TABLE IF NOT EXISTS ebay_taxonomy_categories (
 marketplace VARCHAR(32) NOT NULL,
 category_tree_id VARCHAR(32) NOT NULL,
 category_tree_version VARCHAR(64) NOT NULL,
 category_id VARCHAR(64) NOT NULL,
 category_name TEXT NOT NULL,
 parent_category_id VARCHAR(64),
 leaf BOOLEAN NOT NULL,
 full_path TEXT NOT NULL,
 depth INTEGER NOT NULL,
 last_synced_at TIMESTAMP NOT NULL,
 source VARCHAR(64) NOT NULL,
 PRIMARY KEY (marketplace, category_tree_id, category_id)
);
CREATE INDEX IF NOT EXISTS ix_ebay_taxonomy_categories_name ON ebay_taxonomy_categories(category_name);
CREATE INDEX IF NOT EXISTS ix_ebay_taxonomy_categories_leaf ON ebay_taxonomy_categories(leaf);
