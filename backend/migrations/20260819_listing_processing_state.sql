-- Durable listing-level recovery metadata for stalled backlog repair.
alter table listings add column if not exists processing_state varchar(32) not null default 'queued';
alter table listings add column if not exists processing_stage varchar(64);
alter table listings add column if not exists processing_started_at timestamp;
alter table listings add column if not exists processing_last_attempt_at timestamp;
alter table listings add column if not exists processing_last_success_at timestamp;
alter table listings add column if not exists processing_next_retry_at timestamp;
alter table listings add column if not exists processing_attempt_count integer not null default 0;
alter table listings add column if not exists processing_last_error text;
alter table listings add column if not exists processing_error_stage varchar(64);
alter table listings add column if not exists processing_blocking_reason text;
alter table listings add column if not exists processing_worker_id varchar(255);
alter table listings add column if not exists processing_lease_expires_at timestamp;
create index if not exists ix_listings_processing_state on listings(processing_state);
create index if not exists ix_listings_processing_stage on listings(processing_stage);
create index if not exists ix_listings_processing_next_retry_at on listings(processing_next_retry_at);
create index if not exists ix_listings_processing_worker_id on listings(processing_worker_id);

create table if not exists listing_processing_events (
    id serial primary key,
    listing_id integer not null references listings(id),
    user_id integer not null references users(id),
    event_type varchar(64) not null,
    status varchar(64) not null default 'completed',
    stage varchar(64),
    message text,
    details_json json,
    created_at timestamp not null default now(),
    updated_at timestamp not null default now()
);
create index if not exists ix_listing_processing_events_listing_id on listing_processing_events(listing_id);
create index if not exists ix_listing_processing_events_user_id on listing_processing_events(user_id);
create index if not exists ix_listing_processing_events_event_type on listing_processing_events(event_type);
create index if not exists ix_listing_processing_events_stage on listing_processing_events(stage);
