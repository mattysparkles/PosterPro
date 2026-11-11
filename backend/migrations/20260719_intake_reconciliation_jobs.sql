-- Durable intake polling and reconciliation state. This migration is additive.
-- Rollback/repair: stop intake workers first, then drop intake_reconciliation_jobs
-- followed by intake_source_states only if their audit/recovery records are no
-- longer needed. Existing intake media, canonical items, and listings are untouched.

create table if not exists intake_source_states (
    id bigserial primary key,
    user_id integer not null references users(id),
    provider varchar(64) not null,
    source_key varchar(512) not null,
    provider_cursor text,
    last_successful_poll_at timestamp,
    last_complete_page varchar(255),
    lookback_started_at timestamp,
    last_integrity_scan_at timestamp,
    poll_error text,
    consecutive_failures integer not null default 0,
    next_scheduled_at timestamp,
    metadata_json json,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create unique index if not exists uq_intake_source_states_user_provider_source
    on intake_source_states(user_id, provider, source_key);
create index if not exists ix_intake_source_states_next_scheduled
    on intake_source_states(next_scheduled_at);

create table if not exists intake_reconciliation_jobs (
    id bigserial primary key,
    user_id integer not null references users(id),
    source_state_id integer references intake_source_states(id),
    source_media_id integer references intake_photos(id),
    job_type varchar(64) not null default 'reconcile_media',
    idempotency_key varchar(255) not null,
    interval_key varchar(255),
    status varchar(64) not null default 'queued',
    progress integer not null default 0,
    retry_count integer not null default 0,
    max_retries integer not null default 3,
    last_error text,
    payload_json json,
    result_json json,
    lease_owner varchar(255),
    lease_expires_at timestamp,
    heartbeat_at timestamp,
    acknowledged_at timestamp,
    run_after timestamp,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create unique index if not exists uq_intake_reconciliation_jobs_user_idempotency
    on intake_reconciliation_jobs(user_id, idempotency_key);
create index if not exists ix_intake_reconciliation_jobs_claim
    on intake_reconciliation_jobs(status, run_after, lease_expires_at);
create index if not exists ix_intake_reconciliation_jobs_interval
    on intake_reconciliation_jobs(user_id, interval_key, status);

-- Provider events are idempotent at the database boundary, not only in Python.
create unique index if not exists uq_intake_photos_user_provider_media
    on intake_photos(user_id, source_provider, source_photo_id);
