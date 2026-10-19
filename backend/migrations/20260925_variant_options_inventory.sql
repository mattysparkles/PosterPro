-- First-class canonical listing variants/options and reusable presets.
-- Additive only; existing parent listings remain unchanged.
CREATE TABLE IF NOT EXISTS option_presets (
    id SERIAL PRIMARY KEY,
    scope VARCHAR(16) NOT NULL DEFAULT 'tenant',
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(160) NOT NULL,
    axes JSONB,
    default_price DOUBLE PRECISION,
    price_modifiers JSONB,
    sku_pattern VARCHAR(255),
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_option_presets_scope_user_name UNIQUE (scope, user_id, name)
);
CREATE INDEX IF NOT EXISTS ix_option_presets_scope ON option_presets(scope);
CREATE INDEX IF NOT EXISTS ix_option_presets_user_id ON option_presets(user_id);

CREATE TABLE IF NOT EXISTS product_options (
    id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_product_options_listing_name UNIQUE (listing_id, name)
);
CREATE INDEX IF NOT EXISTS ix_product_options_listing_id ON product_options(listing_id);

CREATE TABLE IF NOT EXISTS product_option_values (
    id SERIAL PRIMARY KEY,
    option_id INTEGER NOT NULL REFERENCES product_options(id) ON DELETE CASCADE,
    value VARCHAR(160) NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_product_option_values_option_value UNIQUE (option_id, value)
);
CREATE INDEX IF NOT EXISTS ix_product_option_values_option_id ON product_option_values(option_id);

CREATE TABLE IF NOT EXISTS listing_variants (
    id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    sku VARCHAR(255) NOT NULL,
    barcode VARCHAR(255),
    quantity_on_hand INTEGER NOT NULL DEFAULT 0,
    quantity_reserved INTEGER NOT NULL DEFAULT 0,
    price DOUBLE PRECISION,
    compare_at_price DOUBLE PRECISION,
    cost DOUBLE PRECISION,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    variant_images JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_listing_variants_listing_sku UNIQUE (listing_id, sku)
);
CREATE INDEX IF NOT EXISTS ix_listing_variants_listing_id ON listing_variants(listing_id);
CREATE INDEX IF NOT EXISTS ix_listing_variants_sku ON listing_variants(sku);

CREATE TABLE IF NOT EXISTS variant_option_values (
    id SERIAL PRIMARY KEY,
    variant_id INTEGER NOT NULL REFERENCES listing_variants(id) ON DELETE CASCADE,
    option_value_id INTEGER NOT NULL REFERENCES product_option_values(id) ON DELETE CASCADE,
    CONSTRAINT uq_variant_option_values_link UNIQUE (variant_id, option_value_id)
);
CREATE INDEX IF NOT EXISTS ix_variant_option_values_variant_id ON variant_option_values(variant_id);
CREATE INDEX IF NOT EXISTS ix_variant_option_values_option_value_id ON variant_option_values(option_value_id);

CREATE TABLE IF NOT EXISTS marketplace_variants (
    id SERIAL PRIMARY KEY,
    variant_id INTEGER NOT NULL REFERENCES listing_variants(id) ON DELETE CASCADE,
    marketplace VARCHAR(32) NOT NULL,
    marketplace_account_id INTEGER REFERENCES marketplace_accounts(id) ON DELETE SET NULL,
    remote_listing_id VARCHAR(255),
    remote_variant_id VARCHAR(255),
    remote_sku VARCHAR(255),
    last_confirmed_quantity INTEGER,
    last_confirmed_price DOUBLE PRECISION,
    last_synced_at TIMESTAMP,
    sync_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    last_error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_marketplace_variants_identity UNIQUE (variant_id, marketplace, marketplace_account_id)
);
CREATE INDEX IF NOT EXISTS ix_marketplace_variants_variant_id ON marketplace_variants(variant_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_variants_remote_listing_id ON marketplace_variants(remote_listing_id);

CREATE TABLE IF NOT EXISTS variant_inventory_events (
    id SERIAL PRIMARY KEY,
    variant_id INTEGER NOT NULL REFERENCES listing_variants(id) ON DELETE CASCADE,
    marketplace VARCHAR(32) NOT NULL,
    external_event_id VARCHAR(255) NOT NULL,
    quantity_delta INTEGER NOT NULL,
    quantity_after INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'applied',
    details JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_variant_inventory_events_external UNIQUE (marketplace, external_event_id)
);
CREATE INDEX IF NOT EXISTS ix_variant_inventory_events_variant_id ON variant_inventory_events(variant_id);
CREATE INDEX IF NOT EXISTS ix_variant_inventory_events_marketplace ON variant_inventory_events(marketplace);
