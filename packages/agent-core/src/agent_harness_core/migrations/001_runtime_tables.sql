create table if not exists runtime_runs (
    run_id text primary key,
    workflow_name text not null,
    status text not null,
    input_text text not null,
    metadata jsonb not null default '{}'::jsonb,
    output_payload jsonb,
    error_text text,
    scheduled_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    cancel_requested_at timestamptz,
    attempt_count integer not null default 0,
    worker_id text,
    lease_expires_at timestamptz,
    event_sequence integer not null default 0
);

create index if not exists idx_runtime_runs_status_created_at
    on runtime_runs (status, created_at);

create index if not exists idx_runtime_runs_lease_expires_at
    on runtime_runs (lease_expires_at);

create table if not exists runtime_run_events (
    event_id text primary key,
    run_id text not null references runtime_runs (run_id) on delete cascade,
    sequence integer not null,
    event_type text not null,
    category text not null,
    node_name text,
    tool_name text,
    model_name text,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    unique (run_id, sequence)
);

create index if not exists idx_runtime_run_events_run_sequence
    on runtime_run_events (run_id, sequence);

create table if not exists runtime_run_usage (
    usage_id text primary key,
    run_id text not null references runtime_runs (run_id) on delete cascade,
    event_id text not null references runtime_run_events (event_id) on delete cascade,
    model_name text,
    input_tokens integer not null default 0,
    output_tokens integer not null default 0,
    total_tokens integer not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists idx_runtime_run_usage_run_id
    on runtime_run_usage (run_id, created_at);
