-- Additive lineage for replacing broad recovery-review buckets with bounded
-- sequence-derived child groups.  No intake or marketplace table is changed.
alter table media_recovery_item_groups add column if not exists parent_group_id integer references media_recovery_item_groups(id);
create index if not exists ix_media_recovery_group_parent on media_recovery_item_groups(parent_group_id);
