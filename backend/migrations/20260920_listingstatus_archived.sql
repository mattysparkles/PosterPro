-- Keep the database enum aligned with the application ListingStatus model.
-- Reconciliation archives machine-owned duplicate drafts recoverably; this is
-- not a destructive delete and is intentionally idempotent for deployments
-- where the value already exists.
ALTER TYPE listingstatus ADD VALUE IF NOT EXISTS 'archived';
