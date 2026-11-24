CREATE TABLE IF NOT EXISTS vine_image_recovery_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    listing_id INTEGER NULL REFERENCES listings(id),
    asin VARCHAR(16) NOT NULL,
    title_hint VARCHAR(512) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP NULL,
    bridge_job_id VARCHAR(128) NULL,
    page_evidence_json JSON NULL,
    candidates_json JSON NULL,
    last_error TEXT NULL,
    completed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_vine_image_recovery_user_asin UNIQUE (user_id, asin)
);
CREATE INDEX IF NOT EXISTS ix_vine_image_recovery_jobs_user_id ON vine_image_recovery_jobs(user_id);
CREATE INDEX IF NOT EXISTS ix_vine_image_recovery_jobs_listing_id ON vine_image_recovery_jobs(listing_id);
CREATE INDEX IF NOT EXISTS ix_vine_image_recovery_jobs_asin ON vine_image_recovery_jobs(asin);
CREATE INDEX IF NOT EXISTS ix_vine_image_recovery_jobs_status ON vine_image_recovery_jobs(status);
CREATE INDEX IF NOT EXISTS ix_vine_image_recovery_jobs_bridge_job_id ON vine_image_recovery_jobs(bridge_job_id);
