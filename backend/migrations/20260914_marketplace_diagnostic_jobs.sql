-- Diagnostics are tenant-scoped extension jobs, but do not belong to a
-- production listing. Keep listing_id nullable for those diagnostic rows.
ALTER TABLE marketplace_extension_jobs
  ALTER COLUMN listing_id DROP NOT NULL;
