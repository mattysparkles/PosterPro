-- Additive scoped-admission metadata.  The global provider circuit remains
-- authoritative for every workload except an explicitly enabled row below.
ALTER TABLE ai_request_reservations
    ADD COLUMN IF NOT EXISTS workload VARCHAR(96),
    ADD COLUMN IF NOT EXISTS work_item_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_workload ON ai_request_reservations (workload);
CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_work_item_id ON ai_request_reservations (work_item_id);

CREATE TABLE IF NOT EXISTS ai_workload_admissions (
    workload VARCHAR(96) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    daily_cap_tokens INTEGER NOT NULL,
    emergency_stop BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO ai_workload_admissions (workload, enabled, daily_cap_tokens, emergency_stop, reason)
VALUES ('GOOGLE_PHOTO_TIMELINE', FALSE, 2000000, FALSE, 'Scoped override disabled pending explicit operator authorization')
ON CONFLICT (workload) DO NOTHING;
