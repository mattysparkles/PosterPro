ALTER TABLE mercari_taxonomy_versions
  ADD COLUMN IF NOT EXISTS source_completeness VARCHAR(16) NOT NULL DEFAULT 'partial';
ALTER TABLE mercari_taxonomy_versions
  ADD COLUMN IF NOT EXISTS root_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mercari_taxonomy_versions
  ADD COLUMN IF NOT EXISTS max_depth INTEGER NOT NULL DEFAULT 0;

ALTER TABLE mercari_taxonomy_categories
  ADD COLUMN IF NOT EXISTS leaf_status VARCHAR(20) NOT NULL DEFAULT 'unknown';

UPDATE mercari_taxonomy_categories
SET leaf_status = CASE WHEN is_leaf THEN 'unknown' ELSE 'branch' END
WHERE leaf_status = 'unknown';

UPDATE mercari_taxonomy_versions v
SET root_count = stats.root_count,
    max_depth = stats.max_depth
FROM (
  SELECT version_id,
         COUNT(*) FILTER (WHERE depth = 1)::INTEGER AS root_count,
         COALESCE(MAX(depth), 0)::INTEGER AS max_depth
  FROM mercari_taxonomy_categories
  GROUP BY version_id
) AS stats
WHERE stats.version_id = v.version_id;
