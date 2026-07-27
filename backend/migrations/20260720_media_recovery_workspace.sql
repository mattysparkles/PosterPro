-- Isolated recovery workspace.  No historical intake assignment, batch,
-- canonical-item, draft, listing, or marketplace row is altered by this DDL.
create table if not exists media_recovery_runs (
    id bigserial primary key,
    user_id integer not null references users(id),
    run_key varchar(128) not null unique,
    pipeline_version varchar(64) not null,
    source_roots_json json,
    processing_status varchar(32) not null default 'running',
    imported_media_count integer not null default 0,
    group_count integer not null default 0,
    draft_count integer not null default 0,
    result_json json,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create table if not exists media_recovery_media (
    id bigserial primary key,
    run_id integer not null references media_recovery_runs(id),
    absolute_path text not null,
    relative_path text not null,
    sha256 varchar(64) not null,
    perceptual_hash varchar(64),
    file_metadata_json json,
    slate_evidence_json json,
    duplicate_of_media_id integer references media_recovery_media(id),
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp,
    constraint uq_media_recovery_media_path unique (run_id, absolute_path)
);
create table if not exists media_recovery_item_groups (
    id bigserial primary key,
    run_id integer not null references media_recovery_runs(id),
    recovery_item_id varchar(255) not null,
    grouping_status varchar(32) not null,
    grouping_confidence float,
    media_paths_json json,
    evidence_json json,
    analysis_json json,
    draft_listing_id integer references listings(id),
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp,
    constraint uq_media_recovery_group_item unique (run_id, recovery_item_id)
);
create index if not exists ix_media_recovery_media_run_hash on media_recovery_media(run_id, sha256);
create index if not exists ix_media_recovery_group_run_status on media_recovery_item_groups(run_id, grouping_status);
