-- Isolated, audited browser taxonomy inspection jobs. These never enter the
-- marketplace publication queue and have no listing foreign key by design.
CREATE TABLE IF NOT EXISTS marketplace_taxonomy_test_sessions (
  session_id VARCHAR(64) PRIMARY KEY,
  owner_user_id INTEGER NOT NULL,
  authorized_device_id INTEGER NOT NULL,
  status VARCHAR(24) NOT NULL,
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  cancelled_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_test_sessions_owner
  ON marketplace_taxonomy_test_sessions(owner_user_id,status,expires_at);

CREATE TABLE IF NOT EXISTS marketplace_taxonomy_test_jobs (
  job_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  owner_user_id INTEGER NOT NULL,
  authorized_device_id INTEGER NOT NULL,
  marketplace VARCHAR(24) NOT NULL,
  command VARCHAR(40) NOT NULL,
  parameters_json TEXT NOT NULL,
  status VARCHAR(24) NOT NULL,
  result_json TEXT,
  error_code VARCHAR(100),
  expires_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  claimed_at TIMESTAMP,
  completed_at TIMESTAMP,
  cancelled_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_test_jobs_claim
  ON marketplace_taxonomy_test_jobs(authorized_device_id,status,expires_at,created_at);
CREATE INDEX IF NOT EXISTS ix_taxonomy_test_jobs_owner
  ON marketplace_taxonomy_test_jobs(owner_user_id,created_at);
CREATE INDEX IF NOT EXISTS ix_taxonomy_test_jobs_session
  ON marketplace_taxonomy_test_jobs(session_id,status,created_at);

CREATE TABLE IF NOT EXISTS marketplace_taxonomy_test_job_events (
  event_id VARCHAR(64) PRIMARY KEY,
  job_id VARCHAR(64) NOT NULL,
  owner_user_id INTEGER NOT NULL,
  actor_type VARCHAR(16) NOT NULL,
  actor_id INTEGER,
  event_type VARCHAR(32) NOT NULL,
  event_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_taxonomy_test_events_job
  ON marketplace_taxonomy_test_job_events(job_id,created_at);
