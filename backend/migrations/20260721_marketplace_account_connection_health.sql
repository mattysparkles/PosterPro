-- Additive connection diagnostics for durable, encrypted marketplace accounts.
alter table marketplace_accounts add column if not exists connection_status varchar(32) not null default 'connected';
alter table marketplace_accounts add column if not exists last_error text;
alter table marketplace_accounts add column if not exists last_refresh_at timestamp;
alter table marketplace_accounts add column if not exists last_successful_check_at timestamp;
create index if not exists ix_marketplace_accounts_connection_status on marketplace_accounts (connection_status);
