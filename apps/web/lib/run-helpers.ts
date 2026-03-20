import type { RunEvent, RunRecord, RunStatus } from "./generated/contracts";

const terminalStatuses = new Set<RunStatus>(["completed", "failed", "cancelled"]);

export type WorkflowNodeTone =
  | "idle"
  | "ready"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type WorkflowNodeView = {
  id: string;
  label: string;
  caption: string;
  tone: WorkflowNodeTone;
};

export function isTerminalStatus(status: RunStatus): boolean {
  return terminalStatuses.has(status);
}

export function formatStatusLabel(status: string): string {
  return status.replaceAll("_", " ").replace(/\b\w/g, (segment) => segment.toUpperCase());
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Unavailable";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unavailable";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}

export function formatRelativeTime(value: string | null | undefined): string {
  if (!value) {
    return "Unavailable";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unavailable";
  }

  const deltaSeconds = Math.round((date.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  if (Math.abs(deltaSeconds) < 60) {
    return formatter.format(deltaSeconds, "second");
  }

  const deltaMinutes = Math.round(deltaSeconds / 60);
  if (Math.abs(deltaMinutes) < 60) {
    return formatter.format(deltaMinutes, "minute");
  }

  const deltaHours = Math.round(deltaMinutes / 60);
  if (Math.abs(deltaHours) < 24) {
    return formatter.format(deltaHours, "hour");
  }

  return formatter.format(Math.round(deltaHours / 24), "day");
}

export function formatDuration(
  startedAt: string | null | undefined,
  completedAt: string | null | undefined,
  updatedAt: string | null | undefined,
): string {
  if (!startedAt) {
    return "Not started";
  }

  const started = new Date(startedAt);
  const completed = new Date(completedAt ?? updatedAt ?? startedAt);

  if (Number.isNaN(started.getTime()) || Number.isNaN(completed.getTime())) {
    return "Unavailable";
  }

  const totalSeconds = Math.max(0, Math.round((completed.getTime() - started.getTime()) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (minutes === 0) {
    return `${seconds}s`;
  }

  return `${minutes}m ${seconds}s`;
}

export function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

export function summarizeRuns(runs: RunRecord[]): {
  totalRuns: number;
  activeRuns: number;
  queuedRuns: number;
  terminalRuns: number;
  completedRuns: number;
  failedRuns: number;
  successRate: string;
} {
  const totalRuns = runs.length;
  const queuedRuns = runs.filter((run) => run.status === "queued").length;
  const activeRuns = runs.filter((run) => run.status === "running" || run.status === "cancelling").length;
  const terminalRuns = runs.filter((run) => isTerminalStatus(run.status)).length;
  const completedRuns = runs.filter((run) => run.status === "completed").length;
  const failedRuns = runs.filter((run) => run.status === "failed" || run.status === "cancelled").length;
  const successRate =
    terminalRuns === 0 ? "n/a" : `${Math.round((completedRuns / terminalRuns) * 100)}%`;

  return {
    totalRuns,
    activeRuns,
    queuedRuns,
    terminalRuns,
    completedRuns,
    failedRuns,
    successRate,
  };
}

export function summarizeRunMetrics(run: RunRecord | null, events: RunEvent[]): {
  eventCount: number;
  nodeCount: number;
  toolCalls: number;
  modelCalls: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  activeNode: string;
} {
  let inputTokens = 0;
  let outputTokens = 0;
  let totalTokens = 0;

  for (const event of events) {
    if (event.event_type !== "model.completed") {
      continue;
    }

    const usage = event.payload.usage as Record<string, unknown> | undefined;
    inputTokens += Number(usage?.input_tokens ?? 0);
    outputTokens += Number(usage?.output_tokens ?? 0);
    totalTokens += Number(usage?.total_tokens ?? 0);
  }

  return {
    eventCount: events.length,
    nodeCount: new Set(events.filter((event) => event.node_name).map((event) => event.node_name)).size,
    toolCalls: events.filter((event) => event.category === "tool" && event.event_type.endsWith(".completed"))
      .length,
    modelCalls: events.filter((event) => event.category === "model" && event.event_type.endsWith(".completed"))
      .length,
    inputTokens,
    outputTokens,
    totalTokens,
    activeNode: findActiveNode(run, events),
  };
}

export function buildWorkflowNodes(run: RunRecord | null, events: RunEvent[]): WorkflowNodeView[] {
  return [
    {
      id: "intake",
      label: "Run Intake",
      caption: run ? formatStatusLabel(run.status) : "Waiting for snapshot",
      tone: run ? "completed" : "idle",
    },
    buildOperationalNode(run, events, "normalize_input", "Normalize Input", "Whitespace tool"),
    buildOperationalNode(run, events, "generate_response", "Generate Response", "Echo model"),
    {
      id: "settlement",
      label: "Finalize",
      caption: run?.completed_at ? formatDateTime(run.completed_at) : "Awaiting terminal status",
      tone: terminalTone(run),
    },
  ];
}

export function mergeEventStream(current: RunEvent[], nextEvent: RunEvent): RunEvent[] {
  if (current.some((event) => event.event_id === nextEvent.event_id)) {
    return current;
  }

  return [...current, nextEvent].sort((left, right) => left.sequence - right.sequence);
}

export function buildFailureSummary(run: RunRecord, events: RunEvent[]): string {
  if (run.error) {
    return run.error;
  }

  const terminalEvent = [...events]
    .reverse()
    .find((event) => event.event_type === "run.failed" || event.event_type === "run.cancelled");

  if (!terminalEvent) {
    return "No failure details recorded.";
  }

  const reason = terminalEvent.payload.error ?? terminalEvent.payload.reason;
  return typeof reason === "string" ? reason : "No failure details recorded.";
}

export function formatPayloadPreview(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (entries.length === 0) {
    return "No payload";
  }

  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(" • ");
}

function buildOperationalNode(
  run: RunRecord | null,
  events: RunEvent[],
  nodeName: string,
  label: string,
  fallbackCaption: string,
): WorkflowNodeView {
  const started = events.some(
    (event) => event.node_name === nodeName && event.event_type === "node.started",
  );
  const completed = events.some(
    (event) => event.node_name === nodeName && event.event_type === "node.completed",
  );
  const nodeEvents = events.filter((event) => event.node_name === nodeName);
  const lastEvent = nodeEvents.at(-1);

  let tone: WorkflowNodeTone = started ? "running" : "idle";
  if (completed) {
    tone = "completed";
  } else if (run?.status === "cancelled" && started) {
    tone = "cancelled";
  } else if (run?.status === "failed" && started) {
    tone = "failed";
  } else if (started) {
    tone = "running";
  } else if (events.some((event) => event.event_type === "run.started")) {
    tone = "ready";
  }

  const caption = lastEvent
    ? [lastEvent.tool_name, lastEvent.model_name, formatRelativeTime(lastEvent.created_at)]
        .filter(Boolean)
        .join(" • ")
    : fallbackCaption;

  return {
    id: nodeName,
    label,
    caption,
    tone,
  };
}

function terminalTone(run: RunRecord | null): WorkflowNodeTone {
  if (!run) {
    return "idle";
  }
  if (run.status === "completed") {
    return "completed";
  }
  if (run.status === "failed") {
    return "failed";
  }
  if (run.status === "cancelled") {
    return "cancelled";
  }
  if (run.status === "running" || run.status === "cancelling") {
    return "running";
  }
  if (run.status === "queued") {
    return "ready";
  }
  return "idle";
}

function findActiveNode(run: RunRecord | null, events: RunEvent[]): string {
  const completedNodes = new Set(
    events.filter((event) => event.event_type === "node.completed" && event.node_name).map((event) => event.node_name),
  );
  const activeNode = [...events]
    .reverse()
    .find(
      (event) =>
        event.event_type === "node.started" &&
        event.node_name &&
        !completedNodes.has(event.node_name),
    );

  if (activeNode?.node_name) {
    return activeNode.node_name;
  }

  if (!run || isTerminalStatus(run.status)) {
    return "None";
  }

  return run.status === "queued" ? "Queued" : "Waiting for event";
}
