alter table runtime_runs
    add column if not exists max_attempts integer not null default 3,
    add column if not exists timeout_seconds integer not null default 300;

create index if not exists idx_runtime_runs_status_scheduled_at
    on runtime_runs (status, scheduled_at);
