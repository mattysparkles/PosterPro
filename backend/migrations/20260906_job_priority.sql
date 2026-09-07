ALTER TABLE marketplace_import_jobs
  ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS requested_by INTEGER NULL;

ALTER TABLE marketplace_crosspost_jobs
  ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS requested_by INTEGER NULL;

CREATE INDEX IF NOT EXISTS ix_marketplace_import_jobs_priority_due
  ON marketplace_import_jobs (priority, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS ix_marketplace_crosspost_jobs_priority_due
  ON marketplace_crosspost_jobs (priority, next_attempt_at, created_at);
