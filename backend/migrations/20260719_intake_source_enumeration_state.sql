-- Persist provider-enumeration completeness separately from per-media imports.
-- Additive/idempotent repair migration; no existing media or listing data changes.
alter table intake_source_states add column if not exists pagination_token text;
alter table intake_source_states add column if not exists last_poll_started_at timestamp;
alter table intake_source_states add column if not exists last_poll_completed_at timestamp;
alter table intake_source_states add column if not exists last_full_enumeration_at timestamp;
alter table intake_source_states add column if not exists enumeration_generation integer not null default 0;
alter table intake_source_states add column if not exists scan_complete boolean not null default false;
alter table intake_source_states add column if not exists enumerated_count integer not null default 0;
alter table intake_source_states add column if not exists new_count integer not null default 0;
alter table intake_source_states add column if not exists changed_count integer not null default 0;
alter table intake_source_states add column if not exists skipped_budget_count integer not null default 0;
alter table intake_source_states add column if not exists oldest_capture_at timestamp;
alter table intake_source_states add column if not exists newest_capture_at timestamp;
