-- Physical package labels are durable jobs just like the existing slate labels.
-- Additive and safe for databases that already have historical print jobs.
ALTER TABLE inventory_print_jobs ADD COLUMN IF NOT EXISTS package_id INTEGER REFERENCES inventory_packages(id);
CREATE INDEX IF NOT EXISTS ix_inventory_print_jobs_package_id ON inventory_print_jobs(package_id);
