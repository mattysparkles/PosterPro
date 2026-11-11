-- Durable provider discovery is intentionally separate from image processing.
create table if not exists intake_provider_media (
    id bigserial primary key,
    user_id integer not null references users(id),
    source_state_id integer not null references intake_source_states(id),
    provider varchar(64) not null,
    source_key varchar(512) not null,
    provider_media_id varchar(512) not null,
    provider_url text,
    preview_url text,
    original_filename varchar(512),
    provider_order integer,
    captured_at timestamp,
    uploaded_at timestamp,
    metadata_fingerprint varchar(128) not null,
    discovery_generation integer not null default 0,
    first_seen_at timestamp default current_timestamp,
    last_seen_at timestamp default current_timestamp,
    processing_status varchar(64) not null default 'discovered',
    processing_error text,
    retry_count integer not null default 0,
    intake_photo_id integer references intake_photos(id),
    metadata_json json,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create unique index if not exists uq_intake_provider_media_identity
    on intake_provider_media(user_id, provider, source_key, provider_media_id);
create index if not exists ix_intake_provider_media_processing
    on intake_provider_media(user_id, source_state_id, processing_status, id);

alter table intake_source_states add column if not exists enumeration_complete boolean not null default false;
alter table intake_source_states add column if not exists enumeration_interrupted boolean not null default false;
alter table intake_source_states add column if not exists discovery_persisted_count integer not null default 0;
alter table intake_source_states add column if not exists provider_visible_count integer;
alter table intake_source_states add column if not exists processing_backlog_count integer not null default 0;
alter table intake_source_states add column if not exists processing_complete boolean not null default false;
alter table intake_source_states add column if not exists reconciliation_backlog_count integer not null default 0;
alter table intake_source_states add column if not exists source_caught_up boolean not null default false;
alter table intake_source_states add column if not exists poll_lease_owner varchar(255);
alter table intake_source_states add column if not exists poll_lease_acquired_at timestamp;
alter table intake_source_states add column if not exists poll_lease_heartbeat_at timestamp;
alter table intake_source_states add column if not exists poll_lease_expires_at timestamp;
alter table intake_source_states add column if not exists poll_cancellation_requested boolean not null default false;
alter table intake_source_states add column if not exists enumeration_status varchar(64);
alter table intake_source_states add column if not exists enumeration_progress_json json;
alter table intake_provider_media add column if not exists processing_lease_owner varchar(255);
alter table intake_provider_media add column if not exists processing_lease_expires_at timestamp;
create index if not exists ix_intake_source_states_poll_lease
    on intake_source_states(user_id, provider, source_key, poll_lease_expires_at);
