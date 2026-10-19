-- Additive controls and durable, versioned photo evidence for isolated media recovery.
alter table media_recovery_runs add column if not exists draft_creation_state varchar(48) not null default 'frozen_for_quality_audit';
create index if not exists ix_media_recovery_runs_draft_creation_state on media_recovery_runs(draft_creation_state);

create table if not exists media_recovery_photo_evidence (
    id bigserial primary key,
    run_id integer not null references media_recovery_runs(id),
    recovery_group_id integer not null references media_recovery_item_groups(id),
    media_id integer not null references media_recovery_media(id),
    listing_id integer references listings(id),
    pipeline_version varchar(96) not null,
    photo_role varchar(64) not null default 'unclassified',
    ocr_text text,
    barcode_attempts_json json,
    decoded_barcode_type varchar(32),
    decoded_barcode_value varchar(255),
    brand varchar(255), product_name varchar(255), model varchar(255), mpn varchar(255), manufacturer_sku varchar(255),
    upc varchar(32), ean varchar(32), gtin varchar(32), isbn varchar(32),
    specifications_json json, packaging_identity text, included_components_json json, damage_json json,
    condition_evidence text, measurement_evidence text, testing_evidence text, evidence_json json,
    confidence float, extraction_method varchar(64) not null default 'deterministic', error_status varchar(64),
    created_at timestamp default current_timestamp, updated_at timestamp default current_timestamp,
    constraint uq_media_recovery_photo_evidence_version unique (recovery_group_id, media_id, pipeline_version)
);
create index if not exists ix_media_recovery_photo_evidence_run_group on media_recovery_photo_evidence(run_id, recovery_group_id);
create index if not exists ix_media_recovery_photo_evidence_identifier on media_recovery_photo_evidence(decoded_barcode_value, model, mpn);
