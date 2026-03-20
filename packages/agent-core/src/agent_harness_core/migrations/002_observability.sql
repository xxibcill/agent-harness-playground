alter table runtime_runs
    add column if not exists trace_id text,
    add column if not exists traceparent text;

create index if not exists idx_runtime_runs_trace_id
    on runtime_runs (trace_id);

alter table runtime_run_events
    add column if not exists trace_id text,
    add column if not exists span_id text,
    add column if not exists parent_span_id text;

create index if not exists idx_runtime_run_events_trace_id
    on runtime_run_events (trace_id, sequence);
