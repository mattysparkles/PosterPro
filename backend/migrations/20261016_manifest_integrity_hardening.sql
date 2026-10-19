-- Never amend an already applied migration: manifest identity is tote scoped.
ALTER TABLE inventory_manifest_imports ADD COLUMN IF NOT EXISTS request_fingerprint VARCHAR(64);
ALTER TABLE inventory_manifest_imports ADD COLUMN IF NOT EXISTS reconciliation_json JSON;
ALTER TABLE inventory_manifest_imports DROP CONSTRAINT IF EXISTS uq_inventory_manifest_policy;
ALTER TABLE inventory_manifest_imports ADD CONSTRAINT uq_inventory_manifest_tote_policy UNIQUE(user_id, tote_id, checksum, duplicate_policy);
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_manifest_operation ON inventory_manifest_imports(user_id, operation_id);
