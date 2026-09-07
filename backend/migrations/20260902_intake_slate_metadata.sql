alter table if exists intake_slates
    add column if not exists metadata_json json;

update intake_slates
set metadata_json = coalesce(metadata_json, '{}'::json)
where metadata_json is null;
