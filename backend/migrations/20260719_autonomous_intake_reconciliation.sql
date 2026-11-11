-- Canonical, provider-neutral intake layer. Existing intake_* tables remain
-- the compatibility projection used by the live queue and listing workflow.

create table if not exists canonical_items (
    id bigserial primary key,
    user_id integer not null references users(id),
    item_id varchar(255) not null,
    inventory_sku varchar(255),
    current_listing_id integer references listings(id),
    current_slate_id integer references intake_slates(id),
    status varchar(64) not null default 'provisional',
    confidence float,
    metadata_json json,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create unique index if not exists uq_canonical_items_user_item_id on canonical_items(user_id, item_id);
create index if not exists ix_canonical_items_user_id on canonical_items(user_id);
create index if not exists ix_canonical_items_status on canonical_items(status);

create table if not exists slate_observations (
    id bigserial primary key,
    user_id integer not null references users(id),
    canonical_item_id integer references canonical_items(id),
    intake_slate_id integer references intake_slates(id),
    media_id integer references intake_photos(id),
    item_id varchar(255) not null,
    observation_type varchar(64) not null default 'original',
    template_version integer,
    confidence float,
    capture_timestamp timestamp,
    raw_qr_json json,
    raw_ocr_json json,
    parsed_values_json json,
    reconciliation_status varchar(64) not null default 'resolved',
    operator_confirmed boolean not null default false,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create unique index if not exists uq_slate_observations_media_id on slate_observations(media_id) where media_id is not null;
create index if not exists ix_slate_observations_user_id on slate_observations(user_id);
create index if not exists ix_slate_observations_item_id on slate_observations(item_id);
create index if not exists ix_slate_observations_canonical_item_id on slate_observations(canonical_item_id);

create table if not exists canonical_item_facts (
    id bigserial primary key,
    canonical_item_id integer not null references canonical_items(id),
    field_name varchar(128) not null,
    value_json json,
    normalized_value text,
    source_type varchar(64) not null,
    source_identifier varchar(255),
    confidence float,
    value_status varchar(32) not null default 'inferred',
    precedence integer not null default 0,
    is_locked boolean not null default false,
    is_current boolean not null default true,
    conflict_state varchar(64),
    effective_at timestamp,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create index if not exists ix_canonical_item_facts_item_field on canonical_item_facts(canonical_item_id, field_name);
create index if not exists ix_canonical_item_facts_current on canonical_item_facts(canonical_item_id, is_current);

create table if not exists intake_reconciliation_events (
    id bigserial primary key,
    user_id integer not null references users(id),
    event_type varchar(64) not null,
    status varchar(64) not null default 'completed',
    source_media_id integer references intake_photos(id),
    canonical_item_id integer references canonical_items(id),
    interval_json json,
    details_json json,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create index if not exists ix_intake_reconciliation_events_user_id on intake_reconciliation_events(user_id);
create index if not exists ix_intake_reconciliation_events_source_media_id on intake_reconciliation_events(source_media_id);

create table if not exists intake_notifications (
    id bigserial primary key,
    user_id integer not null references users(id),
    canonical_item_id integer references canonical_items(id),
    notification_type varchar(64) not null,
    title varchar(255) not null,
    message text,
    href varchar(512),
    metadata_json json,
    read_at timestamp,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);
create index if not exists ix_intake_notifications_user_id on intake_notifications(user_id);
create index if not exists ix_intake_notifications_read_at on intake_notifications(read_at);

-- Extra media chronology is stored in metadata_json for backwards compatibility.
-- This migration deliberately does not mutate existing published listings.
