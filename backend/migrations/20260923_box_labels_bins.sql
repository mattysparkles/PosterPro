create table if not exists inventory_print_jobs (
  id serial primary key,
  user_id integer not null references users(id),
  slate_id integer null references intake_slates(id),
  listing_id integer null references listings(id),
  job_type varchar(64) not null,
  copies integer not null default 2,
  paper_profile varchar(64) not null default 'receipt_58mm',
  status varchar(32) not null default 'queued',
  payload_json json null,
  error text null,
  claimed_at timestamp null,
  completed_at timestamp null,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);
create index if not exists ix_inventory_print_jobs_user_status on inventory_print_jobs(user_id, status);

create table if not exists inventory_bins (
  id serial primary key,
  user_id integer not null references users(id),
  code varchar(128) not null,
  nfc_token varchar(128) not null unique,
  label varchar(255) null,
  metadata_json json null,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now(),
  unique(user_id, code)
);
create index if not exists ix_inventory_bins_user_code on inventory_bins(user_id, code);

create table if not exists inventory_bin_assignments (
  id serial primary key,
  user_id integer not null references users(id),
  bin_id integer not null references inventory_bins(id),
  listing_id integer null references listings(id),
  slate_id integer null references intake_slates(id),
  item_id varchar(255) null,
  active boolean not null default true,
  assigned_at timestamp not null default now(),
  unassigned_at timestamp null,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);
create index if not exists ix_inventory_bin_assignments_active on inventory_bin_assignments(bin_id, active);
create index if not exists ix_inventory_bin_assignments_listing on inventory_bin_assignments(listing_id, active);

create table if not exists inventory_bin_revisions (
  id serial primary key,
  user_id integer not null references users(id),
  listing_id integer null references listings(id),
  slate_id integer null references intake_slates(id),
  item_id varchar(255) null,
  from_bin_code varchar(128) null,
  to_bin_code varchar(128) null,
  reason varchar(255) not null default 'operator_reassignment',
  changed_by integer null references users(id),
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);
create index if not exists ix_inventory_bin_revisions_item on inventory_bin_revisions(user_id, item_id, created_at);
