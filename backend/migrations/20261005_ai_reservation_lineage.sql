-- Durable non-secret provenance for every paid AI reservation.  This is
-- additive and safe to apply before the code that writes the new fields.
ALTER TABLE ai_request_reservations
    ADD COLUMN IF NOT EXISTS item_group_id INTEGER,
    ADD COLUMN IF NOT EXISTS intake_batch_id INTEGER,
    ADD COLUMN IF NOT EXISTS photo_id INTEGER,
    ADD COLUMN IF NOT EXISTS worker_task_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS model VARCHAR(96),
    ADD COLUMN IF NOT EXISTS operation VARCHAR(96),
    ADD COLUMN IF NOT EXISTS request_id VARCHAR(128),
    ADD COLUMN IF NOT EXISTS input_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_item_group_id ON ai_request_reservations (item_group_id);
CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_intake_batch_id ON ai_request_reservations (intake_batch_id);
CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_photo_id ON ai_request_reservations (photo_id);
CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_worker_task_id ON ai_request_reservations (worker_task_id);
CREATE INDEX IF NOT EXISTS ix_ai_request_reservations_operation ON ai_request_reservations (operation);
