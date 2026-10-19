-- Durable deterministic recovery evidence only. This migration does not alter
-- intake photo assignments, batches, drafts, listings, or marketplace state.
create table if not exists intake_slate_recovery_candidates (
    id bigserial primary key,
    user_id integer not null references users(id),
    intake_photo_id integer not null references intake_photos(id),
    pipeline_version varchar(64) not null,
    classification varchar(32) not null,
    raw_item_id varchar(255),
    normalized_item_id varchar(255),
    box_id varchar(255),
    location varchar(255),
    quantity varchar(64),
    condition varchar(64),
    notes text,
    stored_qr_payload_json json,
    stored_ocr_text text,
    evidence_json json,
    classification_confidence float,
    item_id_confidence float,
    match_status varchar(32) not null default 'unresolved',
    matched_canonical_item_id integer references canonical_items(id),
    matched_intake_slate_id integer references intake_slates(id),
    matched_batch_id integer references intake_photo_batches(id),
    review_status varchar(32) not null default 'pending',
    accepted_rejected_state varchar(32) not null default 'unreviewed',
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp,
    constraint uq_intake_slate_recovery_candidate_version
        unique (user_id, intake_photo_id, pipeline_version)
);
create unique index if not exists uq_intake_slate_recovery_candidate_version
    on intake_slate_recovery_candidates(user_id, intake_photo_id, pipeline_version);
-- A prior interrupted application could have created the table/index but not
-- the named constraint. Adopt that index when present; this block is safe to
-- rerun and remains additive.
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'uq_intake_slate_recovery_candidate_version'
          and conrelid = 'intake_slate_recovery_candidates'::regclass
    ) then
        if exists (
            select 1 from pg_class
            where relname = 'uq_intake_slate_recovery_candidate_version'
              and relkind = 'i'
        ) then
            alter table intake_slate_recovery_candidates
                add constraint uq_intake_slate_recovery_candidate_version
                unique using index uq_intake_slate_recovery_candidate_version;
        else
            alter table intake_slate_recovery_candidates
                add constraint uq_intake_slate_recovery_candidate_version
                unique (user_id, intake_photo_id, pipeline_version);
        end if;
    end if;
end $$;
create index if not exists ix_intake_slate_recovery_candidate_match
    on intake_slate_recovery_candidates(user_id, match_status, normalized_item_id);
