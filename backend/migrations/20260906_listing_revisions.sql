CREATE TABLE IF NOT EXISTS listing_revisions (
  id SERIAL PRIMARY KEY,
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  user_id INTEGER NULL REFERENCES users(id),
  revision INTEGER NOT NULL DEFAULT 1,
  operation VARCHAR(32) NOT NULL DEFAULT 'save',
  changed_fields JSONB,
  marketplaces_targeted JSONB,
  marketplace_results JSONB,
  sync_state VARCHAR(32) NOT NULL DEFAULT 'local',
  status VARCHAR(32) NOT NULL DEFAULT 'recorded',
  reason TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_listing_revisions_listing_id ON listing_revisions(listing_id);
CREATE INDEX IF NOT EXISTS ix_listing_revisions_user_id ON listing_revisions(user_id);
CREATE INDEX IF NOT EXISTS ix_listing_revisions_revision ON listing_revisions(revision);
CREATE INDEX IF NOT EXISTS ix_listing_revisions_status ON listing_revisions(status);
