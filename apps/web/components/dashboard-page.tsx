"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition, useEffect, useState } from "react";

import { createRun, listRuns } from "../lib/api";
import type { CreateRunRequest, RunRecord } from "../lib/generated/contracts";
import {
  formatDateTime,
  formatDuration,
  formatRelativeTime,
  formatStatusLabel,
  summarizeRuns,
} from "../lib/run-helpers";

const defaultMetadata = `{
  "origin": "web"
}`;

type LauncherState = {
  workflow: string;
  input: string;
  metadataText: string;
  model: string;
  maxTokens: string;
  baseUrl: string;
  clientTimeoutSeconds: string;
  scheduledAt: string;
  maxAttempts: string;
  timeoutSeconds: string;
};

const initialLauncherState: LauncherState = {
  workflow: "demo.echo",
  input: "",
  metadataText: defaultMetadata,
  model: "",
  maxTokens: "",
  baseUrl: "",
  clientTimeoutSeconds: "",
  scheduledAt: "",
  maxAttempts: "3",
  timeoutSeconds: "300",
};

export function DashboardPage() {
  const router = useRouter();
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [launcher, setLauncher] = useState<LauncherState>(initialLauncherState);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadRuns() {
      try {
        const nextRuns = await listRuns();
        if (!active) {
          return;
        }
        setRuns(nextRuns);
        setRefreshError(null);
      } catch (error) {
        if (!active) {
          return;
        }
        setRefreshError(error instanceof Error ? error.message : "Unable to load runs.");
      } finally {
        if (active) {
          setIsLoading(false);
        }
      }
    }

    void loadRuns();
    const intervalId = window.setInterval(() => {
      void loadRuns();
    }, 4000);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setIsSubmitting(true);

    try {
      const metadata = parseMetadata(launcher.metadataText);
      const trimmedInput = launcher.input.trim();
      const maxAttempts = Number(launcher.maxAttempts);
      const timeoutSeconds = Number(launcher.timeoutSeconds);
      const workflowConfig = buildWorkflowConfig(launcher);
      if (!trimmedInput) {
        throw new Error("Prompt is required.");
      }
      if (!Number.isInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 10) {
        throw new Error("Max attempts must be between 1 and 10.");
      }
      if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 5 || timeoutSeconds > 3600) {
        throw new Error("Timeout must be between 5 and 3600 seconds.");
      }
      const request: CreateRunRequest = {
        workflow: launcher.workflow.trim() || "demo.echo",
        input: trimmedInput,
        metadata,
        workflow_config: workflowConfig,
        scheduled_at: launcher.scheduledAt ? new Date(launcher.scheduledAt).toISOString() : null,
        max_attempts: maxAttempts,
        timeout_seconds: timeoutSeconds,
      };
      const createdRun = await createRun(request);
      setRuns((currentRuns) => [createdRun, ...currentRuns]);
      setLauncher(initialLauncherState);
      startTransition(() => {
        router.push(`/runs/${createdRun.run_id}`);
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Unable to create the run.");
    } finally {
      setIsSubmitting(false);
    }
  }

  const summary = summarizeRuns(runs);
  const recentRuns = runs.slice(0, 8);
  const failureRuns = runs
    .filter((run) => run.status === "failed" || run.status === "cancelled")
    .slice(0, 4);
  const isModelWorkflow = launcher.workflow === "anthropic.respond";
  const workflowHint =
    launcher.workflow === "anthropic.respond"
      ? "Model-backed workflow using Claude. Ensure ANTHROPIC_AUTH_TOKEN is configured."
      : launcher.workflow === "demo.react"
        ? "Basic ReAct loop that plans, uses local tools, observes the result, and then answers."
        : "Demo workflow that echoes input. Useful for testing without API keys.";

  return (
    <main className="app-shell">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Task 09 operator surface</p>
          <h1>Operator console for live agent runs</h1>
          <p className="lede">
            Launch demo or model-backed executions, watch workflow state transitions over SSE, and
            inspect provider-backed results from the same operator surface.
          </p>
        </div>
        <div className="hero-meta">
          <span className="hero-chip">Pure client Next.js</span>
          <span className="hero-chip">Streaming run updates</span>
          <span className="hero-chip">Historical detail routes</span>
        </div>
      </section>

      <section className="dashboard-grid">
        <article className="panel launcher-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Run launcher</p>
              <h2>Start a new workflow run</h2>
            </div>
            <span className="status-chip status-chip--queued">Ready</span>
          </div>
          <form className="launcher-form" onSubmit={handleSubmit}>
            <label className="field">
              <span>Workflow</span>
              <select
                value={launcher.workflow}
                onChange={(event) =>
                  setLauncher((current) => ({ ...current, workflow: event.target.value }))
                }
              >
                <option value="demo.echo">demo.echo (Demo mode - no API required)</option>
                <option value="demo.react">demo.react (Basic ReAct loop with local tools)</option>
                <option value="anthropic.respond">anthropic.respond (Anthropic Claude - requires API key)</option>
              </select>
              <span className="field-hint">{workflowHint}</span>
            </label>
            <label className="field">
              <span>Prompt</span>
              <textarea
                rows={6}
                placeholder="Summarize the operator signals for this run."
                value={launcher.input}
                onChange={(event) =>
                  setLauncher((current) => ({ ...current, input: event.target.value }))
                }
                required
              />
            </label>
            {isModelWorkflow ? (
              <>
                <div className="field-row">
                  <label className="field">
                    <span>Model</span>
                    <input
                      placeholder="claude-sonnet-4-5"
                      value={launcher.model}
                      onChange={(event) =>
                        setLauncher((current) => ({ ...current, model: event.target.value }))
                      }
                    />
                    <span className="field-hint">
                      Stored in the shared workflow config and passed through to the worker.
                    </span>
                  </label>
                  <label className="field">
                    <span>Max tokens</span>
                    <input
                      inputMode="numeric"
                      min={1}
                      max={8192}
                      onChange={(event) =>
                        setLauncher((current) => ({ ...current, maxTokens: event.target.value }))
                      }
                      placeholder="Uses worker default"
                      type="number"
                      value={launcher.maxTokens}
                    />
                  </label>
                </div>
                <div className="field-row">
                  <label className="field">
                    <span>Provider base URL</span>
                    <input
                      placeholder="Uses worker default"
                      value={launcher.baseUrl}
                      onChange={(event) =>
                        setLauncher((current) => ({ ...current, baseUrl: event.target.value }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Client timeout (seconds)</span>
                    <input
                      inputMode="numeric"
                      min={1}
                      max={3600}
                      onChange={(event) =>
                        setLauncher((current) => ({
                          ...current,
                          clientTimeoutSeconds: event.target.value,
                        }))
                      }
                      placeholder="Uses worker default"
                      type="number"
                      value={launcher.clientTimeoutSeconds}
                    />
                  </label>
                </div>
              </>
            ) : (
              <p className="field-hint">
                Demo runs do not require provider configuration. Switch to
                `anthropic.respond` to supply model, token, and runtime override settings.
              </p>
            )}
            <div className="field-row">
              <label className="field">
                <span>Schedule at</span>
                <input
                  type="datetime-local"
                  value={launcher.scheduledAt}
                  onChange={(event) =>
                    setLauncher((current) => ({ ...current, scheduledAt: event.target.value }))
                  }
                />
              </label>
              <label className="field">
                <span>Max attempts</span>
                <input
                  inputMode="numeric"
                  min={1}
                  max={10}
                  onChange={(event) =>
                    setLauncher((current) => ({ ...current, maxAttempts: event.target.value }))
                  }
                  type="number"
                  value={launcher.maxAttempts}
                />
              </label>
            </div>
            <div className="field-row">
              <label className="field">
                <span>Timeout (seconds)</span>
                <input
                  inputMode="numeric"
                  min={5}
                  max={3600}
                  onChange={(event) =>
                    setLauncher((current) => ({ ...current, timeoutSeconds: event.target.value }))
                  }
                  type="number"
                  value={launcher.timeoutSeconds}
                />
              </label>
            </div>
            <label className="field">
              <span>Run metadata</span>
              <textarea
                rows={7}
                value={launcher.metadataText}
                onChange={(event) =>
                  setLauncher((current) => ({ ...current, metadataText: event.target.value }))
                }
              />
            </label>
            {formError ? <p className="inline-error">{formError}</p> : null}
            <button className="primary-button" disabled={isSubmitting} type="submit">
              {isSubmitting ? "Creating run..." : "Create run"}
            </button>
          </form>
        </article>

        <article className="panel summary-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Operator metrics</p>
              <h2>Queue and terminal health</h2>
            </div>
            <span className="section-caption">
              {isLoading ? "Loading..." : `Updated ${formatRelativeTime(new Date().toISOString())}`}
            </span>
          </div>
          <div className="metric-grid">
            <MetricCard label="Total runs" value={String(summary.totalRuns)} accent="accent" />
            <MetricCard label="Queued" value={String(summary.queuedRuns)} accent="queued" />
            <MetricCard label="Active" value={String(summary.activeRuns)} accent="running" />
            <MetricCard label="Success rate" value={summary.successRate} accent="completed" />
          </div>
          <div className="summary-copy">
            <p>
              {summary.terminalRuns === 0
                ? "No terminal runs recorded yet."
                : `${summary.completedRuns} completed and ${summary.failedRuns} failed or cancelled.`}
            </p>
            {refreshError ? <p className="inline-error">{refreshError}</p> : null}
          </div>
        </article>
      </section>

      <section className="dashboard-grid dashboard-grid--wide">
        <article className="panel table-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Recent runs</p>
              <h2>Execution history</h2>
            </div>
            <Link className="ghost-link" href="/">
              Dashboard
            </Link>
          </div>
          {recentRuns.length === 0 ? (
            <EmptyState
              title="No runs yet"
              copy="Create a run to start building history, metrics, and live event data."
            />
          ) : (
            <div className="table-wrap">
              <table className="run-table">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Status</th>
                    <th>Workflow</th>
                    <th>Created</th>
                    <th>Duration</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {recentRuns.map((run) => (
                    <tr key={run.run_id}>
                      <td>
                        <div className="table-primary">{run.run_id}</div>
                        <div className="table-secondary">{run.input}</div>
                      </td>
                      <td>
                        <StatusChip status={run.status} />
                      </td>
                      <td>{run.workflow}</td>
                      <td>
                        <div className="table-primary">{formatDateTime(run.created_at)}</div>
                        <div className="table-secondary">{formatRelativeTime(run.created_at)}</div>
                      </td>
                      <td>{formatDuration(run.started_at, run.completed_at, run.updated_at)}</td>
                      <td className="table-action">
                        <Link className="table-link" href={`/runs/${run.run_id}`}>
                          Inspect
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>

        <aside className="panel failure-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Failure inspection</p>
              <h2>Runs that need attention</h2>
            </div>
          </div>
          {failureRuns.length === 0 ? (
            <EmptyState
              title="No failures recorded"
              copy="The dashboard will surface failed and cancelled runs here with direct links."
            />
          ) : (
            <div className="failure-list">
              {failureRuns.map((run) => (
                <article className="failure-card" key={run.run_id}>
                  <div className="failure-card__header">
                    <StatusChip status={run.status} />
                    <span className="table-secondary">{formatRelativeTime(run.updated_at)}</span>
                  </div>
                  <h3>{run.run_id}</h3>
                  <p>{run.error ?? "Cancellation was recorded without an explicit error message."}</p>
                  <dl className="failure-card__meta">
                    <div>
                      <dt>Workflow</dt>
                      <dd>{run.workflow}</dd>
                    </div>
                    <div>
                      <dt>Attempt</dt>
                      <dd>{run.attempt_count}</dd>
                    </div>
                  </dl>
                  <Link className="table-link" href={`/runs/${run.run_id}`}>
                    Open detail view
                  </Link>
                </article>
              ))}
            </div>
          )}
        </aside>
      </section>
    </main>
  );
}

function parseMetadata(source: string): Record<string, unknown> {
  if (!source.trim()) {
    return {};
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(source) as unknown;
  } catch {
    throw new Error("Configuration metadata must be valid JSON.");
  }

  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Configuration metadata must be a JSON object.");
  }

  return parsed as Record<string, unknown>;
}

function buildWorkflowConfig(launcher: LauncherState): CreateRunRequest["workflow_config"] {
  const model = launcher.model.trim();
  const baseUrl = launcher.baseUrl.trim();
  const maxTokens = parseOptionalInteger(
    launcher.maxTokens,
    "Max tokens must be between 1 and 8192.",
    1,
    8192,
  );
  const clientTimeoutSeconds = parseOptionalInteger(
    launcher.clientTimeoutSeconds,
    "Client timeout must be between 1 and 3600 seconds.",
    1,
    3600,
  );
  const hasRuntimeOverrides = Boolean(baseUrl) || clientTimeoutSeconds !== null;
  const provider = launcher.workflow === "anthropic.respond" ? "anthropic" : null;

  return {
    provider,
    model: model || null,
    max_tokens: maxTokens,
    runtime_overrides: hasRuntimeOverrides
      ? {
          base_url: baseUrl || null,
          client_timeout_seconds: clientTimeoutSeconds,
        }
      : null,
  };
}

function parseOptionalInteger(
  value: string,
  errorMessage: string,
  min: number,
  max: number,
): number | null {
  if (!value.trim()) {
    return null;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    throw new Error(errorMessage);
  }
  return parsed;
}

function MetricCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent: "accent" | "queued" | "running" | "completed";
}) {
  return (
    <article className={`metric-card metric-card--${accent}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function StatusChip({ status }: { status: RunRecord["status"] }) {
  return (
    <span className={`status-chip status-chip--${status}`}>{formatStatusLabel(status)}</span>
  );
}

function EmptyState({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{copy}</p>
    </div>
  );
}
