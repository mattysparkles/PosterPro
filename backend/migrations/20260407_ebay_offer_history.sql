CREATE TABLE IF NOT EXISTS ebay_offer_history (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER REFERENCES listings(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    ebay_offer_id VARCHAR(255) NOT NULL UNIQUE,
    ebay_listing_id VARCHAR(255),
    buyer_username VARCHAR(255),
    offered_amount FLOAT,
    currency VARCHAR(8),
    offer_status VARCHAR(64),
    decision VARCHAR(32) NOT NULL,
    decision_reason TEXT,
    raw_payload JSON,
    decided_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ebay_offer_history_listing_id ON ebay_offer_history(listing_id);
CREATE INDEX IF NOT EXISTS ix_ebay_offer_history_user_id ON ebay_offer_history(user_id);
CREATE INDEX IF NOT EXISTS ix_ebay_offer_history_offer_id ON ebay_offer_history(ebay_offer_id);
CREATE INDEX IF NOT EXISTS ix_ebay_offer_history_listing_external_id ON ebay_offer_history(ebay_listing_id);
CREATE INDEX IF NOT EXISTS ix_ebay_offer_history_decision ON ebay_offer_history(decision);
