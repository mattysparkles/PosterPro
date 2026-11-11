CREATE TABLE IF NOT EXISTS storefront_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    slug VARCHAR(80) NOT NULL UNIQUE,
    store_name VARCHAR(160) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    logo_url TEXT,
    banner_url TEXT,
    contact_email VARCHAR(255),
    accent_color VARCHAR(16) NOT NULL DEFAULT '#1d4f7a',
    public_settings_json JSONB,
    payment_settings_json JSONB,
    provider_secrets_enc TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_storefront_profiles_user_id ON storefront_profiles(user_id);
CREATE INDEX IF NOT EXISTS ix_storefront_profiles_slug ON storefront_profiles(slug);
CREATE INDEX IF NOT EXISTS ix_storefront_profiles_enabled ON storefront_profiles(enabled);

CREATE TABLE IF NOT EXISTS storefront_affiliate_clicks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    store_id BIGINT NOT NULL REFERENCES storefront_profiles(id) ON DELETE CASCADE,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    marketplace VARCHAR(32) NOT NULL,
    attribution_mode VARCHAR(32) NOT NULL DEFAULT 'none',
    referrer_host VARCHAR(255),
    session_hash VARCHAR(64),
    clicked_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_storefront_affiliate_clicks_user_id ON storefront_affiliate_clicks(user_id);
CREATE INDEX IF NOT EXISTS ix_storefront_affiliate_clicks_store_id ON storefront_affiliate_clicks(store_id);
CREATE INDEX IF NOT EXISTS ix_storefront_affiliate_clicks_listing_id ON storefront_affiliate_clicks(listing_id);
CREATE INDEX IF NOT EXISTS ix_storefront_affiliate_clicks_marketplace ON storefront_affiliate_clicks(marketplace);
CREATE INDEX IF NOT EXISTS ix_storefront_affiliate_clicks_clicked_at ON storefront_affiliate_clicks(clicked_at);

-- Order/payment persistence is deliberately separate from Sale. Creating an
-- order never changes inventory; a verified payment transition must link one
-- canonical Sale before stock/reconciliation side effects are allowed.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'marketplacename') THEN
        ALTER TYPE marketplacename ADD VALUE IF NOT EXISTS 'storefront_direct';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS storefront_orders (
    id BIGSERIAL PRIMARY KEY,
    order_number VARCHAR(40) NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    store_id BIGINT NOT NULL REFERENCES storefront_profiles(id) ON DELETE CASCADE,
    idempotency_key VARCHAR(128) NOT NULL,
    checkout_token TEXT NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'AWAITING_PAYMENT',
    payment_status VARCHAR(40) NOT NULL DEFAULT 'AWAITING_PAYMENT',
    fulfillment_status VARCHAR(40) NOT NULL DEFAULT 'UNFULFILLED',
    tracking_number VARCHAR(120),
    carrier VARCHAR(64),
    shipped_at TIMESTAMP WITHOUT TIME ZONE,
    delivered_at TIMESTAMP WITHOUT TIME ZONE,
    payment_method VARCHAR(32) NOT NULL,
    provider VARCHAR(32) NOT NULL DEFAULT 'manual',
    provider_transaction_id VARCHAR(255),
    customer_name VARCHAR(255) NOT NULL,
    customer_email VARCHAR(255) NOT NULL,
    shipping_address_json JSONB,
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    subtotal DOUBLE PRECISION NOT NULL DEFAULT 0,
    discount_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
    discount_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    total DOUBLE PRECISION NOT NULL DEFAULT 0,
    payment_config_snapshot JSONB,
    reservation_expires_at TIMESTAMP WITHOUT TIME ZONE,
    paid_at TIMESTAMP WITHOUT TIME ZONE,
    confirmed_by BIGINT REFERENCES users(id),
    confirmation_reference VARCHAR(255),
    sale_id BIGINT UNIQUE REFERENCES sales(id),
    internal_note TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_storefront_order_idempotency UNIQUE (user_id, idempotency_key),
    CONSTRAINT uq_storefront_provider_transaction UNIQUE (user_id, provider, provider_transaction_id)
);
CREATE INDEX IF NOT EXISTS ix_storefront_orders_user_id ON storefront_orders(user_id);
CREATE INDEX IF NOT EXISTS ix_storefront_orders_store_id ON storefront_orders(store_id);
CREATE INDEX IF NOT EXISTS ix_storefront_orders_payment_status ON storefront_orders(payment_status);
CREATE INDEX IF NOT EXISTS ix_storefront_orders_fulfillment_status ON storefront_orders(fulfillment_status);
CREATE INDEX IF NOT EXISTS ix_storefront_orders_reservation_expires_at ON storefront_orders(reservation_expires_at);
CREATE INDEX IF NOT EXISTS ix_storefront_orders_customer_email ON storefront_orders(customer_email);

CREATE TABLE IF NOT EXISTS storefront_order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES storefront_orders(id) ON DELETE CASCADE,
    listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    title_snapshot VARCHAR(255) NOT NULL,
    sku_snapshot VARCHAR(255),
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    item_snapshot JSONB
);
CREATE INDEX IF NOT EXISTS ix_storefront_order_items_order_id ON storefront_order_items(order_id);
CREATE INDEX IF NOT EXISTS ix_storefront_order_items_listing_id ON storefront_order_items(listing_id);

CREATE TABLE IF NOT EXISTS storefront_payment_attempts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id BIGINT NOT NULL REFERENCES storefront_orders(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'CREATED',
    provider_transaction_id VARCHAR(255),
    amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    verified_at TIMESTAMP WITHOUT TIME ZONE,
    safe_result_json JSONB,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_storefront_payment_provider_identity UNIQUE (user_id, provider, provider_transaction_id)
);
CREATE INDEX IF NOT EXISTS ix_storefront_payment_attempts_user_id ON storefront_payment_attempts(user_id);
CREATE INDEX IF NOT EXISTS ix_storefront_payment_attempts_order_id ON storefront_payment_attempts(order_id);
CREATE INDEX IF NOT EXISTS ix_storefront_payment_attempts_status ON storefront_payment_attempts(status);
