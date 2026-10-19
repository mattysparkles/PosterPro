ALTER TABLE ai_request_ledger ADD COLUMN IF NOT EXISTS tokens_cached INTEGER;
ALTER TABLE ai_request_ledger ADD COLUMN IF NOT EXISTS total_tokens INTEGER;
ALTER TABLE ai_request_ledger ADD COLUMN IF NOT EXISTS request_id VARCHAR(128);
ALTER TABLE ai_request_ledger ADD COLUMN IF NOT EXISTS service_tier VARCHAR(64);
