-- Repair for an already-applied first version of the additive reconciliation
-- migration. PostgreSQL integer primary keys need a sequence default.

create sequence if not exists canonical_items_id_seq;
alter table canonical_items alter column id set default nextval('canonical_items_id_seq');
select setval('canonical_items_id_seq', coalesce((select max(id) from canonical_items), 1), (select count(*) > 0 from canonical_items));

create sequence if not exists slate_observations_id_seq;
alter table slate_observations alter column id set default nextval('slate_observations_id_seq');
select setval('slate_observations_id_seq', coalesce((select max(id) from slate_observations), 1), (select count(*) > 0 from slate_observations));

create sequence if not exists canonical_item_facts_id_seq;
alter table canonical_item_facts alter column id set default nextval('canonical_item_facts_id_seq');
select setval('canonical_item_facts_id_seq', coalesce((select max(id) from canonical_item_facts), 1), (select count(*) > 0 from canonical_item_facts));

create sequence if not exists intake_reconciliation_events_id_seq;
alter table intake_reconciliation_events alter column id set default nextval('intake_reconciliation_events_id_seq');
select setval('intake_reconciliation_events_id_seq', coalesce((select max(id) from intake_reconciliation_events), 1), (select count(*) > 0 from intake_reconciliation_events));

create sequence if not exists intake_notifications_id_seq;
alter table intake_notifications alter column id set default nextval('intake_notifications_id_seq');
select setval('intake_notifications_id_seq', coalesce((select max(id) from intake_notifications), 1), (select count(*) > 0 from intake_notifications));
