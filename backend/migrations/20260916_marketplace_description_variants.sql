ALTER TABLE listings ADD COLUMN IF NOT EXISTS canonical_description TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS marketplace_descriptions JSONB;
UPDATE listings SET canonical_description = description
WHERE canonical_description IS NULL AND description IS NOT NULL;
