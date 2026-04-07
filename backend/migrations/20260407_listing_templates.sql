CREATE TABLE IF NOT EXISTS listing_templates (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    category_id VARCHAR(255),
    is_category_default BOOLEAN NOT NULL DEFAULT FALSE,
    fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_listing_templates_user_id ON listing_templates(user_id);
CREATE INDEX IF NOT EXISTS ix_listing_templates_name ON listing_templates(name);
CREATE INDEX IF NOT EXISTS ix_listing_templates_category_id ON listing_templates(category_id);
CREATE INDEX IF NOT EXISTS ix_listing_templates_is_category_default ON listing_templates(is_category_default);
