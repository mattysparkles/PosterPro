-- Speeds tenant-scoped stable workflow-state notification dedupe without
-- rewriting or removing historical notification rows.
create index concurrently if not exists ix_intake_notifications_user_type_href_created
    on intake_notifications (user_id, notification_type, href, created_at desc);
