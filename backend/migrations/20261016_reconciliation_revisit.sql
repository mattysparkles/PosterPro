-- An unresolved decision is a pending intent, not a terminal decision.
ALTER TABLE inventory_reconciliation_decisions DROP CONSTRAINT IF EXISTS uq_inventory_reconciliation_entry;
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_reconciliation_terminal_entry
  ON inventory_reconciliation_decisions(manifest_entry_id) WHERE status = 'applied';
