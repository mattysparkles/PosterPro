CREATE TABLE IF NOT EXISTS inventory_reconciliation_decisions (
 id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), manifest_entry_id INTEGER NOT NULL REFERENCES inventory_manifest_entries(id), action VARCHAR(32) NOT NULL, quantity INTEGER NOT NULL CHECK (quantity >= 0), source_location_key VARCHAR(128), destination_location_key VARCHAR(128) NOT NULL, operation_id VARCHAR(64) NOT NULL, request_fingerprint VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'applied', created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now(),
 CONSTRAINT uq_inventory_reconciliation_entry UNIQUE(manifest_entry_id), CONSTRAINT uq_inventory_reconciliation_operation UNIQUE(user_id, operation_id)
);
ALTER TABLE inventory_stock ADD CONSTRAINT ck_inventory_stock_reserved_le_on_hand CHECK (reserved <= on_hand) NOT VALID;
