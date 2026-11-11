-- Prevent stale editor/worker ORM snapshots from replacing newer Listing
-- fields or JSON payloads. SQLAlchemy includes version_id in UPDATE predicates
-- and increments it on every ORM update.
ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS version_id INTEGER NOT NULL DEFAULT 1;
