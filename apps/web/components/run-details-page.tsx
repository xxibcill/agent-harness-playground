"use client";

import Link from "next/link";
import { startTransition, useEffect, useRef, useState } from "react";

import {
  buildRunEventsStreamUrl,
  cancelRun,
  fetchRun,
  fetchRunEvents,
} from "../lib/api";
import type { RunEvent, RunRecord } from "../lib/generated/contracts";
import {
  buildFailureSummary,
  buildWorkflowNodes,
  formatDateTime,
  formatDuration,
  formatJson,
  formatPayloadPreview,
  formatRelativeTime,
  formatStatusLabel,
  isTerminalStatus,
  mergeEventStream,
  summarizeRunMetrics,
  type WorkflowNodeTone,
} from "../lib/run-helpers";

type StreamState = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export function RunDetailsPage({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunRecord | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCancelling, setIsCancelling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [streamState, setStreamState] = useState<StreamState>("idle");
  const [streamAttempt, setStreamAttempt] = useState(0);
  const lastSequenceRef = useRef(0);
  const reconnectTimerRef = useRef<number | null>(null);

  async function refreshRunSnapshot() {
    try {
      const nextRun = await fetchRun(runId);
      setRun(nextRun);
      if (isTerminalStatus(nextRun.status)) {
        setStreamState("closed");
      }
    } catch {
      return;
    }
  }

  useEffect(() => {
    let active = true;

    async function loadRunDetail() {
      setIsLoading(true);
      setLoadError(null);

      try {
        const [nextRun, nextEvents] = await Promise.all([fetchRun(runId), fetchRunEvents(runId)]);
        if (!active) {
          return;
        }
        lastSequenceRef.current = nextEvents.at(-1)?.sequence ?? 0;
        setRun(nextRun);
        setEvents(nextEvents);
        setStreamState(isTerminalStatus(nextRun.status) ? "closed" : "connecting");
      } catch (error) {
        if (!active) {
          return;
        }
        setLoadError(error instanceof Error ? error.message : "Unable to load the run.");
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    }

    void loadRunDetail();

    return () => {
      active = false;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
    };
  }, [runId]);

  function handleIncomingEvent(nextEvent: RunEvent) {
    lastSequenceRef.current = Math.max(lastSequenceRef.current, nextEvent.sequence);
    setEvents((currentEvents) => mergeEventStream(currentEvents, nextEvent));
    if (nextEvent.event_type.startsWith("run.") || nextEvent.event_type === "workflow.completed") {
      void refreshRunSnapshot();
    }
    if (
      nextEvent.event_type === "run.completed" ||
      nextEvent.event_type === "run.failed" ||
      nextEvent.event_type === "run.cancelled"
    ) {
      setStreamState("closed");
    }
  }

  useEffect(() => {
    if (!run || isTerminalStatus(run.status)) {
      return;
    }

    const eventSource = new EventSource(buildRunEventsStreamUrl(runId, lastSequenceRef.current));
    setStreamState("connecting");

    const onRunEvent = (message: Event) => {
      const payload = JSON.parse((message as MessageEvent<string>).data) as RunEvent;
      startTransition(() => {
        setStreamState("live");
        handleIncomingEvent(payload);
      });
    };

    eventSource.addEventListener("run-event", onRunEvent as EventListener);
    eventSource.onerror = () => {
      eventSource.close();
      setStreamState("reconnecting");
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      reconnectTimerRef.current = window.setTimeout(() => {
        setStreamAttempt((currentAttempt) => currentAttempt + 1);
      }, 1500);
    };

    return () => {
      eventSource.close();
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [run?.status, runId, streamAttempt]);

  async function handleCancel() {
    setIsCancelling(true);
    setActionError(null);

    try {
      const nextRun = await cancelRun(runId);
      setRun(nextRun);
      if (isTerminalStatus(nextRun.status)) {
        setStreamState("closed");
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unable to cancel the run.");
    } finally {
      setIsCancelling(false);
    }
  }

  const metrics = summarizeRunMetrics(run, events);
  const workflowNodes = buildWorkflowNodes(run, events);
  const failureSummary = run ? buildFailureSummary(run, events) : null;
  const traceEvents = events.filter((event) => event.trace_id || event.span_id);

  return (
    <main className="app-shell">
      <section className="detail-header panel">
        <div className="detail-header__copy">
          <Link className="ghost-link" href="/">
            Back to dashboard
          </Link>
          <p className="section-label">Historical run detail</p>
          <h1>{run?.run_id ?? runId}</h1>
          <p className="lede">
            Live state follows the API event stream. Historical state is loaded from the same run
            record and event history endpoints.
          </p>
        </div>
        <div className="detail-header__actions">
          {run ? <StatusChip status={run.status} /> : null}
          <span className={`stream-chip stream-chip--${streamState}`}>{formatStreamState(streamState)}</span>
          <button
            className="secondary-button"
            onClick={() => void refreshRunSnapshot()}
            type="button"
          >
            Refresh snapshot
          </button>
          <button
            className="primary-button"
            disabled={!run || isCancelling || isTerminalStatus(run.status)}
            onClick={() => void handleCancel()}
            type="button"
          >
            {isCancelling ? "Cancelling..." : "Cancel run"}
          </button>
        </div>
      </section>

      {loadError ? <p className="inline-error inline-error--standalone">{loadError}</p> : null}
      {actionError ? <p className="inline-error inline-error--standalone">{actionError}</p> : null}

      <section className="detail-metric-grid">
        <MetricPanel
          label="Lifecycle"
          value={run ? formatStatusLabel(run.status) : "Loading"}
          detail={run ? `${run.attempt_count} attempt(s)` : "Awaiting snapshot"}
        />
        <MetricPanel
          label="Elapsed"
          value={run ? formatDuration(run.started_at, run.completed_at, run.updated_at) : "Loading"}
          detail={run?.started_at ? `Started ${formatRelativeTime(run.started_at)}` : "Run has not started"}
        />
        <MetricPanel
          label="Events"
          value={String(metrics.eventCount)}
          detail={metrics.activeNode === "None" ? "No active node" : `Active: ${metrics.activeNode}`}
        />
        <MetricPanel
          label="Model usage"
          value={`${metrics.totalTokens} tokens`}
          detail={`${metrics.modelCalls} model call(s) • ${metrics.toolCalls} tool call(s)`}
        />
      </section>

      <section className="detail-grid">
        <article className="panel workflow-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Workflow graph</p>
              <h2>Node states and transitions</h2>
            </div>
            {isLoading ? <span className="section-caption">Loading history...</span> : null}
          </div>
          <WorkflowGraph nodes={workflowNodes} />
        </article>

        <article className="panel summary-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Trace summary</p>
              <h2>Correlation identifiers</h2>
            </div>
          </div>
          <dl className="definition-grid">
            <div>
              <dt>Workflow</dt>
              <dd>{run?.workflow ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Worker</dt>
              <dd>{run?.worker_id ?? "Unassigned"}</dd>
            </div>
            <div>
              <dt>Trace ID</dt>
              <dd>{run?.trace_id ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Traceparent</dt>
              <dd>{run?.traceparent ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDateTime(run?.created_at)}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>{formatDateTime(run?.completed_at)}</dd>
            </div>
          </dl>
        </article>
      </section>

      <section className="detail-grid">
        <article className="panel payload-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Run payloads</p>
              <h2>Input, output, and failure context</h2>
            </div>
          </div>
          <div className="payload-stack">
            <PayloadBlock label="Prompt" value={run?.input ?? "Unavailable"} />
            <PayloadBlock label="Metadata" value={formatJson(run?.metadata ?? {})} />
            <PayloadBlock label="Output" value={formatJson(run?.output ?? {})} />
            {run?.status === "failed" || run?.status === "cancelled" ? (
              <PayloadBlock label="Failure summary" value={failureSummary ?? "Unavailable"} />
            ) : null}
          </div>
        </article>

        <article className="panel trace-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Trace events</p>
              <h2>Span-linked event view</h2>
            </div>
          </div>
          {traceEvents.length === 0 ? (
            <EmptyState
              title="No trace identifiers yet"
              copy="Trace and span data appears on events that were emitted inside traced execution spans."
            />
          ) : (
            <div className="trace-list">
              {traceEvents.map((event) => (
                <article className="trace-card" key={event.event_id}>
                  <div className="trace-card__header">
                    <strong>{event.event_type}</strong>
                    <span>{formatRelativeTime(event.created_at)}</span>
                  </div>
                  <dl>
                    <div>
                      <dt>Trace</dt>
                      <dd>{event.trace_id ?? "Unavailable"}</dd>
                    </div>
                    <div>
                      <dt>Span</dt>
                      <dd>{event.span_id ?? "Unavailable"}</dd>
                    </div>
                    <div>
                      <dt>Parent</dt>
                      <dd>{event.parent_span_id ?? "Root"}</dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          )}
        </article>
      </section>

      <section className="panel timeline-panel">
        <div className="section-heading">
          <div>
            <p className="section-label">Event timeline</p>
            <h2>Ordered execution history</h2>
          </div>
        </div>
        {events.length === 0 ? (
          <EmptyState
            title="No timeline yet"
            copy="Timeline entries appear when the run history endpoint returns stored events."
          />
        ) : (
          <div className="timeline-list">
            {events.map((event) => (
              <details className="timeline-item" key={event.event_id} open={event.sequence === events.at(-1)?.sequence}>
                <summary>
                  <span className="timeline-sequence">#{event.sequence}</span>
                  <span className="timeline-title">{event.event_type}</span>
                  <span className="timeline-copy">{formatPayloadPreview(event.payload)}</span>
                  <span className="timeline-time">{formatDateTime(event.created_at)}</span>
                </summary>
                <div className="timeline-meta">
                  <span>{event.category}</span>
                  {event.node_name ? <span>node={event.node_name}</span> : null}
                  {event.tool_name ? <span>tool={event.tool_name}</span> : null}
                  {event.model_name ? <span>model={event.model_name}</span> : null}
                </div>
                <pre>{formatJson(event.payload)}</pre>
              </details>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function MetricPanel({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="metric-panel">
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function PayloadBlock({ label, value }: { label: string; value: string }) {
  return (
    <section className="payload-block">
      <div className="payload-block__header">
        <h3>{label}</h3>
      </div>
      <pre>{value}</pre>
    </section>
  );
}

function WorkflowGraph({ nodes }: { nodes: ReturnType<typeof buildWorkflowNodes> }) {
  const graphWidth = 760;
  const graphHeight = 220;
  const xPositions = [40, 220, 420, 610];
  const width = 130;
  const height = 82;

  return (
    <div className="workflow-graph">
      <svg
        aria-label="Workflow graph"
        role="img"
        viewBox={`0 0 ${graphWidth} ${graphHeight}`}
      >
        {nodes.slice(0, -1).map((node, index) => (
          <line
            className={`workflow-edge workflow-edge--${edgeTone(nodes[index + 1].tone)}`}
            key={`edge-${node.id}`}
            x1={xPositions[index] + width}
            x2={xPositions[index + 1]}
            y1={110}
            y2={110}
          />
        ))}
        {nodes.map((node, index) => (
          <g className={`workflow-node workflow-node--${node.tone}`} key={node.id}>
            <rect
              height={height}
              rx={24}
              width={width}
              x={xPositions[index]}
              y={68}
            />
            <text x={xPositions[index] + 18} y={105}>
              {node.label}
            </text>
            <text className="workflow-node__caption" x={xPositions[index] + 18} y={128}>
              {node.caption}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function edgeTone(tone: WorkflowNodeTone): "idle" | "active" | "terminal" {
  if (tone === "completed") {
    return "terminal";
  }
  if (tone === "running" || tone === "ready") {
    return "active";
  }
  return "idle";
}

function StatusChip({ status }: { status: RunRecord["status"] }) {
  return (
    <span className={`status-chip status-chip--${status}`}>{formatStatusLabel(status)}</span>
  );
}

function formatStreamState(streamState: StreamState): string {
  switch (streamState) {
    case "connecting":
      return "Connecting";
    case "live":
      return "Live stream";
    case "reconnecting":
      return "Reconnecting";
    case "closed":
      return "Closed";
    default:
      return "Idle";
  }
}

function EmptyState({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{copy}</p>
    </div>
  );
}
