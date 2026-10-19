-- Additive, restart-safe disposition fields for the isolated recovery manifest.
alter table media_recovery_media add column if not exists processing_state varchar(32) not null default 'manifested';
alter table media_recovery_media add column if not exists final_disposition varchar(64);
alter table media_recovery_media add column if not exists assigned_recovery_item_id varchar(255);
create index if not exists ix_media_recovery_media_disposition on media_recovery_media(run_id, final_disposition);
