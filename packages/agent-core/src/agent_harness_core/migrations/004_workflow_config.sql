alter table runtime_runs
    add column if not exists workflow_config jsonb not null default '{}'::jsonb;
