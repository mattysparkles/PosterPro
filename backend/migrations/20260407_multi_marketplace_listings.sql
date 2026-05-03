-- Multi-marketplace expansion for per-channel listing records.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'marketplacelistingstatus') THEN
        CREATE TYPE marketplacelistingstatus AS ENUM ('DRAFT', 'PUBLISHED', 'UPDATED', 'FAILED', 'DELETED', 'PENDING');
    END IF;
END
$$;

ALTER TABLE marketplace_listings
    ADD COLUMN IF NOT EXISTS marketplace_listing_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS raw_response JSON,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();

ALTER TABLE marketplace_listings
    DROP COLUMN IF EXISTS external_listing_id,
    DROP COLUMN IF EXISTS payload;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'marketplace_listings'
          AND column_name = 'status'
          AND udt_name <> 'marketplacelistingstatus'
    ) THEN
        ALTER TABLE marketplace_listings
            ALTER COLUMN status TYPE marketplacelistingstatus USING UPPER(status::text)::marketplacelistingstatus;
    END IF;
END
$$;

ALTER TABLE marketplace_listings
    ALTER COLUMN status SET DEFAULT 'DRAFT';

CREATE INDEX IF NOT EXISTS ix_marketplace_listings_marketplace_listing_id ON marketplace_listings (marketplace_listing_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_listings_listing_marketplace ON marketplace_listings (listing_id, marketplace);
