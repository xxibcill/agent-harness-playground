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

export function formatLatency(latencyMs: number | null | undefined): string {
  if (latencyMs === null || latencyMs === undefined) {
    return "N/A";
  }
  if (latencyMs < 1000) {
    return `${latencyMs}ms`;
  }
  return `${(latencyMs / 1000).toFixed(2)}s`;
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
  latencyMs: number | null;
  requestId: string | null;
  isModelBacked: boolean;
  modelName: string | null;
  modelProvider: string | null;
  hasModelFailure: boolean;
  modelFailureType: string | null;
  modelFailureHint: string | null;
} {
  const storedModel = readObject(run?.output?.model);
  const storedUsage = readObject(storedModel?.usage);

  let inputTokens = readInteger(storedUsage?.input_tokens);
  let outputTokens = readInteger(storedUsage?.output_tokens);
  let totalTokens = readInteger(storedUsage?.total_tokens);
  let latencyMs = readOptionalInteger(storedModel?.latency_ms);
  let requestId = readOptionalString(storedModel?.request_id);
  let isModelBacked = readOptionalString(storedModel?.provider) !== "demo";
  let modelName = readOptionalString(storedModel?.model_name);
  let modelProvider = readOptionalString(storedModel?.provider);
  let hasModelFailure = false;
  let modelFailureType: string | null = null;
  let modelFailureHint: string | null = null;

  for (const event of events) {
    if (event.event_type === "model.completed") {
      const usage = event.payload.usage as Record<string, unknown> | undefined;
      inputTokens += Number(usage?.input_tokens ?? 0);
      outputTokens += Number(usage?.output_tokens ?? 0);
      totalTokens += Number(usage?.total_tokens ?? 0);

      if (event.payload.latency_ms !== undefined) {
        latencyMs = Number(event.payload.latency_ms);
      }
      if (event.payload.request_id && typeof event.payload.request_id === "string") {
        requestId = event.payload.request_id;
      }
      if (event.model_name) {
        modelName = event.model_name;
        if (!event.model_name.startsWith("demo-")) {
          isModelBacked = true;
        }
      }
      if (typeof event.payload.provider === "string") {
        modelProvider = event.payload.provider;
        isModelBacked = event.payload.provider !== "demo";
      } else if (event.model_name) {
        modelProvider = inferProviderFromModelName(event.model_name);
      }
    }

    if (event.event_type === "model.failed") {
      hasModelFailure = true;
      modelFailureType = (event.payload.error_type as string) ?? "unknown_error";
      modelFailureHint = (event.payload.recovery_hint as string) ?? null;
      if (event.model_name) {
        modelName = event.model_name;
      }
      if (typeof event.payload.provider === "string") {
        modelProvider = event.payload.provider;
      }
    }
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
    latencyMs,
    requestId,
    isModelBacked,
    modelName,
    modelProvider,
    hasModelFailure,
    modelFailureType,
    modelFailureHint,
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
  const modelFailure = [...events]
    .reverse()
    .find((event) => event.event_type === "model.failed");
  if (modelFailure) {
    const message =
      typeof modelFailure.payload.error_message === "string"
        ? modelFailure.payload.error_message
        : run.error ?? "No failure details recorded.";

    if (
      modelFailure.payload.recovery_hint &&
      typeof modelFailure.payload.recovery_hint === "string"
    ) {
      return `${message}\n\nRecovery suggestion: ${modelFailure.payload.recovery_hint}`;
    }

    if (modelFailure.payload.error_type === "configuration_error") {
      const configField = modelFailure.payload.config_field as string | undefined;
      if (configField) {
        return `${message}\n\nConfiguration issue: ${configField}`;
      }
    }

    if (modelFailure.payload.error_type === "run_timeout_exceeded") {
      return `${message}\n\nRun timeout exceeded. Increase timeout_seconds or reduce the workflow scope.`;
    }

    return message;
  }

  const timeoutEvent = [...events]
    .reverse()
    .find((event) => event.event_type === "run.timeout_exceeded");
  if (timeoutEvent && typeof timeoutEvent.payload.error === "string") {
    return `${timeoutEvent.payload.error}\n\nRun timeout exceeded. Increase timeout_seconds or reduce the workflow scope.`;
  }

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
  const baseMessage = typeof reason === "string" ? reason : "No failure details recorded.";
  return baseMessage;
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

function readObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readInteger(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function readOptionalInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function inferProviderFromModelName(modelName: string): string | null {
  if (modelName.startsWith("claude-")) {
    return "anthropic";
  }
  if (modelName.startsWith("gpt-")) {
    return "openai";
  }
  if (modelName.startsWith("demo-")) {
    return "demo";
  }
  return null;
}
