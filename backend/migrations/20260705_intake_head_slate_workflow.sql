create table if not exists intake_sessions (
    id integer primary key,
    user_id integer not null references users(id),
    session_id varchar(255) not null unique,
    name varchar(255),
    source_album_id varchar(255),
    source_folder_id varchar(255),
    default_location varchar(255),
    item_prefix varchar(64),
    box_prefix varchar(64),
    status varchar(64) not null default 'active',
    metadata_json json,
    created_at datetime default current_timestamp,
    updated_at datetime default current_timestamp
);
create index if not exists ix_intake_sessions_user_id on intake_sessions(user_id);
create index if not exists ix_intake_sessions_status on intake_sessions(status);
create index if not exists ix_intake_sessions_source_album_id on intake_sessions(source_album_id);
create index if not exists ix_intake_sessions_source_folder_id on intake_sessions(source_folder_id);

create table if not exists intake_slates (
    id integer primary key,
    user_id integer not null references users(id),
    intake_session_id integer references intake_sessions(id),
    session_id varchar(255),
    item_id varchar(255) not null unique,
    box_id varchar(255),
    location varchar(255),
    title varchar(255),
    brand varchar(255),
    model varchar(255),
    condition varchar(64),
    notes text,
    flaws text,
    weight varchar(64),
    length varchar(64),
    width varchar(64),
    height varchar(64),
    packed boolean not null default false,
    internal_notes text,
    qr_payload_json json,
    slate_image_id integer,
    listing_id integer references listings(id),
    status varchar(64) not null default 'draft',
    created_at datetime default current_timestamp,
    updated_at datetime default current_timestamp
);
create index if not exists ix_intake_slates_user_id on intake_slates(user_id);
create index if not exists ix_intake_slates_intake_session_id on intake_slates(intake_session_id);
create index if not exists ix_intake_slates_session_id on intake_slates(session_id);
create index if not exists ix_intake_slates_box_id on intake_slates(box_id);
create index if not exists ix_intake_slates_location on intake_slates(location);
create index if not exists ix_intake_slates_status on intake_slates(status);
create index if not exists ix_intake_slates_slate_image_id on intake_slates(slate_image_id);
create index if not exists ix_intake_slates_listing_id on intake_slates(listing_id);

create table if not exists intake_photo_batches (
    id integer primary key,
    user_id integer not null references users(id),
    intake_session_id integer references intake_sessions(id),
    session_id varchar(255),
    item_id varchar(255) not null,
    slate_id integer references intake_slates(id),
    first_photo_id integer,
    last_photo_id integer,
    photo_count integer not null default 0,
    public_photo_count integer not null default 0,
    internal_photo_count integer not null default 0,
    draft_listing_id integer references listings(id),
    status varchar(64) not null default 'collecting',
    metadata_json json,
    created_at datetime default current_timestamp,
    updated_at datetime default current_timestamp
);
create index if not exists ix_intake_photo_batches_user_id on intake_photo_batches(user_id);
create index if not exists ix_intake_photo_batches_intake_session_id on intake_photo_batches(intake_session_id);
create index if not exists ix_intake_photo_batches_session_id on intake_photo_batches(session_id);
create index if not exists ix_intake_photo_batches_item_id on intake_photo_batches(item_id);
create index if not exists ix_intake_photo_batches_slate_id on intake_photo_batches(slate_id);
create index if not exists ix_intake_photo_batches_first_photo_id on intake_photo_batches(first_photo_id);
create index if not exists ix_intake_photo_batches_last_photo_id on intake_photo_batches(last_photo_id);
create index if not exists ix_intake_photo_batches_draft_listing_id on intake_photo_batches(draft_listing_id);
create index if not exists ix_intake_photo_batches_status on intake_photo_batches(status);

create table if not exists intake_photos (
    id integer primary key,
    user_id integer not null references users(id),
    source_provider varchar(64) not null,
    source_photo_id varchar(512) not null,
    source_album_id varchar(255),
    source_folder_id varchar(255),
    original_filename varchar(512),
    local_path text not null,
    downloaded_url text,
    content_hash varchar(128),
    captured_at datetime,
    uploaded_at datetime,
    imported_at datetime,
    image_type varchar(64),
    is_slate boolean not null default false,
    is_public_listing_candidate boolean not null default true,
    is_internal_only boolean not null default false,
    item_id varchar(255),
    batch_id integer references intake_photo_batches(id),
    metadata_json json,
    created_at datetime default current_timestamp,
    updated_at datetime default current_timestamp
);
create index if not exists ix_intake_photos_user_id on intake_photos(user_id);
create index if not exists ix_intake_photos_source_provider on intake_photos(source_provider);
create index if not exists ix_intake_photos_source_photo_id on intake_photos(source_photo_id);
create index if not exists ix_intake_photos_source_album_id on intake_photos(source_album_id);
create index if not exists ix_intake_photos_source_folder_id on intake_photos(source_folder_id);
create index if not exists ix_intake_photos_content_hash on intake_photos(content_hash);
create index if not exists ix_intake_photos_captured_at on intake_photos(captured_at);
create index if not exists ix_intake_photos_uploaded_at on intake_photos(uploaded_at);
create index if not exists ix_intake_photos_imported_at on intake_photos(imported_at);
create index if not exists ix_intake_photos_image_type on intake_photos(image_type);
create index if not exists ix_intake_photos_is_slate on intake_photos(is_slate);
create index if not exists ix_intake_photos_is_public_listing_candidate on intake_photos(is_public_listing_candidate);
create index if not exists ix_intake_photos_is_internal_only on intake_photos(is_internal_only);
create index if not exists ix_intake_photos_item_id on intake_photos(item_id);
create index if not exists ix_intake_photos_batch_id on intake_photos(batch_id);
