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

const workflowOptions = [
  {
    value: "demo.echo",
    title: "Echo",
    badge: "Step 1",
    description: "Verify core infrastructure: create, queue, execute, stream.",
  },
  {
    value: "demo.route",
    title: "Routing",
    badge: "Step 2",
    description: "Deterministic branching: classify input and route to response.",
  },
  {
    value: "demo.react.once",
    title: "ReAct Once",
    badge: "Step 3",
    description: "Single reason-act cycle: plan, use one tool, respond.",
  },
  {
    value: "demo.react",
    title: "ReAct Loop",
    badge: "Step 4",
    description: "Iterative reasoning: plan, use tools, observe, repeat.",
  },
  {
    value: "demo.tool.single",
    title: "Single Tool",
    badge: "Step 5",
    description: "Call one tool with input from user prompt.",
  },
  {
    value: "demo.tool.select",
    title: "Tool Selection",
    badge: "Step 6",
    description: "Dynamic tool selection based on input analysis.",
  },
  {
    value: "anthropic.respond",
    title: "Provider API",
    badge: "Step 7",
    description: "Use external model API with configurable parameters.",
  },
] as const;

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
  const latestRun = runs[0] ?? null;
  const isModelWorkflow = launcher.workflow === "anthropic.respond";
  const workflowHint =
    launcher.workflow === "anthropic.respond"
      ? "Model-backed workflow using Claude. Ensure ANTHROPIC_AUTH_TOKEN is configured."
      : launcher.workflow === "demo.react"
        ? "Looping ReAct workflow that can plan, use tools, observe results, and iterate multiple times before answering."
        : launcher.workflow === "demo.react.once"
          ? "One-shot ReAct: single reason-act cycle. Plans, uses one tool, then responds."
          : "Demo workflow that echoes input. Useful for testing without API keys.";
  const consoleGuidance = buildConsoleGuidance(summary, latestRun, refreshError);

  return (
    <main className="app-shell app-shell--dashboard">
      <section className="hero-panel hero-panel--dashboard">
        <div className="hero-panel__content">
          <p className="eyebrow">Agent Harness operator console</p>
          <h1>Launch runs, watch state, and inspect the whole execution path.</h1>
          <p className="lede">
            This surface is for operators doing real checks, not a marketing dashboard. Start a
            run, keep the queue readable, and move straight into the live detail stream when
            something changes.
          </p>
          <div className="hero-stat-grid">
            <HeroStat
              label="Runs recorded"
              value={String(summary.totalRuns)}
              detail={summary.totalRuns === 0 ? "No history yet" : "Across the current operator view"}
            />
            <HeroStat
              label="In flight"
              value={String(summary.activeRuns)}
              detail={
                summary.activeRuns === 0
                  ? "Nothing is currently executing"
                  : "Queued, running, or cancelling"
              }
            />
            <HeroStat
              label="Success rate"
              value={summary.successRate}
              detail={
                summary.terminalRuns === 0
                  ? "Computed after the first terminal run"
                  : `${summary.completedRuns} completed / ${summary.terminalRuns} terminal`
              }
            />
          </div>
        </div>

        <div className="hero-rail">
          <article className="hero-rail__card">
            <div className="section-heading section-heading--compact">
              <div>
                <p className="section-label">Console posture</p>
                <h2>Built for fast operational checks</h2>
              </div>
            </div>
            <ul className="hero-list">
              <li>Creates runs against the same-origin `/api/runs` proxy.</li>
              <li>Refreshes recent run state every 4 seconds.</li>
              <li>Hands off to the live SSE detail page immediately after launch.</li>
            </ul>
          </article>

          <article className="hero-rail__card hero-rail__card--accent">
            <p className="section-label">Suggested next move</p>
            <h2>{consoleGuidance.title}</h2>
            <p>{consoleGuidance.copy}</p>
          </article>
        </div>
      </section>

      <section className="dashboard-grid">
        <article className="panel launcher-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Run launcher</p>
              <h2>Create a run and jump straight into live detail</h2>
            </div>
            <span className="status-chip status-chip--queued">Ready</span>
          </div>
          <form className="launcher-form" onSubmit={handleSubmit}>
            <section className="form-section">
              <div className="form-section__header">
                <div>
                  <p className="section-kicker">Workflow</p>
                  <h3>Choose what you want to exercise</h3>
                </div>
                <p>{workflowHint}</p>
              </div>
              <div className="workflow-option-grid">
                {workflowOptions.map((option) => (
                  <button
                    aria-pressed={launcher.workflow === option.value}
                    className={`workflow-option${launcher.workflow === option.value ? " workflow-option--active" : ""}`}
                    key={option.value}
                    onClick={() =>
                      setLauncher((current) => ({ ...current, workflow: option.value }))
                    }
                    type="button"
                  >
                    <span className="workflow-option__badge">{option.badge}</span>
                    <strong>{option.title}</strong>
                    <span>{option.description}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="form-section">
              <div className="form-section__header">
                <div>
                  <p className="section-kicker">Prompt</p>
                  <h3>Describe the run input clearly</h3>
                </div>
                <p>The dashboard will redirect to the detail page as soon as the run is created.</p>
              </div>
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
            </section>

            <section className="form-section form-section--compact">
              <div className="form-section__header">
                <div>
                  <p className="section-kicker">Runtime policy</p>
                  <h3>Set queue timing and retry limits</h3>
                </div>
                <p>These values control scheduling, retry behavior, and the worker timeout budget.</p>
              </div>
              <div className="field-row field-row--triple">
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
            </section>

            {isModelWorkflow ? (
              <section className="form-section form-section--compact">
                <div className="form-section__header">
                  <div>
                    <p className="section-kicker">Provider overrides</p>
                    <h3>Override model and transport settings</h3>
                  </div>
                  <p>Leave fields blank to use worker defaults.</p>
                </div>
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
                      Passed through in the shared workflow config.
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
              </section>
            ) : null}

            <section className="form-section">
              <div className="form-section__header">
                <div>
                  <p className="section-kicker">Metadata</p>
                  <h3>Attach operator context to the run</h3>
                </div>
                <p>JSON only. Include origin, ticket IDs, or any downstream routing hints.</p>
              </div>
              <label className="field">
                <span>Run metadata</span>
                <textarea
                  rows={6}
                  value={launcher.metadataText}
                  onChange={(event) =>
                    setLauncher((current) => ({ ...current, metadataText: event.target.value }))
                  }
                />
              </label>
            </section>

            {formError ? <p className="inline-error">{formError}</p> : null}
            <div className="form-actions">
              <p className="form-note">
                New runs appear in recent history immediately, then open their detail route for live
                event streaming.
              </p>
              <button className="primary-button" disabled={isSubmitting} type="submit">
                {isSubmitting ? "Creating run..." : "Create run and open live detail"}
              </button>
            </div>
          </form>
        </article>

        <aside className="dashboard-sidebar">
          <article className="panel summary-panel">
            <div className="section-heading">
              <div>
                <p className="section-label">Operator metrics</p>
                <h2>Queue posture at a glance</h2>
              </div>
              <span className="section-caption">
                {isLoading ? "Loading..." : "Refreshed continuously"}
              </span>
            </div>
            <div className="metric-grid metric-grid--summary">
              <MetricCard
                accent="accent"
                detail="All visible runs"
                label="Total runs"
                value={String(summary.totalRuns)}
              />
              <MetricCard
                accent="queued"
                detail="Queued or waiting"
                label="Queued"
                value={String(summary.queuedRuns)}
              />
              <MetricCard
                accent="running"
                detail="Currently executing"
                label="Active"
                value={String(summary.activeRuns)}
              />
              <MetricCard
                accent="completed"
                detail="Terminal completion rate"
                label="Success rate"
                value={summary.successRate}
              />
            </div>
            <div className="summary-callout">
              <strong>{consoleGuidance.title}</strong>
              <p>{consoleGuidance.copy}</p>
            </div>
            {refreshError ? <p className="inline-error">{refreshError}</p> : null}
          </article>

          <article className="panel activity-panel">
            <div>
              <p className="section-label">Latest movement</p>
              <h2>{latestRun ? "Most recent run" : "What happens next"}</h2>
            </div>
            {latestRun ? (
              <div className="activity-card">
                <div className="activity-card__header">
                  <StatusChip status={latestRun.status} />
                  <span className="table-secondary">{formatRelativeTime(latestRun.updated_at)}</span>
                </div>
                <h3>{latestRun.workflow}</h3>
                <p>{latestRun.input.trim() || "No prompt captured for this run."}</p>
                <dl className="activity-card__meta">
                  <div>
                    <dt>Created</dt>
                    <dd>{formatDateTime(latestRun.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Duration</dt>
                    <dd>{formatDuration(latestRun.started_at, latestRun.completed_at, latestRun.updated_at)}</dd>
                  </div>
                </dl>
                <Link className="table-link" href={`/runs/${latestRun.run_id}`}>
                  Open live detail view
                </Link>
              </div>
            ) : (
              <EmptyState
                title="Launch a smoke test first"
                copy="Start with `demo.echo` to validate the control plane, then switch to a model-backed run when the queue and event stream look healthy."
              />
            )}
          </article>
        </aside>
      </section>

      <section className="dashboard-grid dashboard-grid--wide">
        <article className="panel table-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">Recent runs</p>
              <h2>Execution history</h2>
            </div>
            <span className="section-caption">
              {isLoading ? "Loading..." : `${recentRuns.length} run${recentRuns.length === 1 ? "" : "s"} shown`}
            </span>
          </div>
          {recentRuns.length === 0 ? (
            <EmptyState
              title="No runs yet"
              copy="Create a run to start building history, queue signals, and the live event timeline."
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

        <aside className="panel watchlist-panel">
          <div className="section-heading">
            <div>
              <p className="section-label">{failureRuns.length === 0 ? "Operator playbook" : "Watchlist"}</p>
              <h2>{failureRuns.length === 0 ? "Recommended verification flow" : "Runs that need attention"}</h2>
            </div>
          </div>
          {failureRuns.length === 0 ? (
            <div className="playbook-list">
              <article className="playbook-item">
                <span className="playbook-item__index">01</span>
                <div>
                  <h3>Smoke test the happy path</h3>
                  <p>Start with `demo.echo` and confirm that the detail page begins streaming events.</p>
                </div>
              </article>
              <article className="playbook-item">
                <span className="playbook-item__index">02</span>
                <div>
                  <h3>Exercise the worker loop</h3>
                  <p>Run `demo.react` when you want to see planning, tool use, and intermediate steps.</p>
                </div>
              </article>
              <article className="playbook-item">
                <span className="playbook-item__index">03</span>
                <div>
                  <h3>Move to the provider-backed path</h3>
                  <p>Use `anthropic.respond` only after credentials and transport defaults are confirmed.</p>
                </div>
              </article>
            </div>
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
  detail,
}: {
  label: string;
  value: string;
  accent: "accent" | "queued" | "running" | "completed";
  detail?: string;
}) {
  return (
    <article className={`metric-card metric-card--${accent}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <p>{detail}</p> : null}
    </article>
  );
}

function HeroStat({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="hero-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
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

function buildConsoleGuidance(
  summary: ReturnType<typeof summarizeRuns>,
  latestRun: RunRecord | null,
  refreshError: string | null,
) {
  if (refreshError) {
    return {
      title: "Reconnect the run feed",
      copy: "The UI is rendering, but the browser could not refresh recent runs. Fix the API connection before trusting queue counts.",
    };
  }

  if (!latestRun) {
    return {
      title: "Run `demo.echo` first",
      copy: "Use the zero-dependency workflow to validate the full create-to-stream path before testing anything provider-backed.",
    };
  }

  if (summary.activeRuns > 0) {
    return {
      title: "Keep an eye on the live stream",
      copy: "At least one run is still in flight. Open the newest detail page to confirm node transitions and output are still advancing.",
    };
  }

  if (summary.failedRuns > 0) {
    return {
      title: "Review the failure watchlist",
      copy: "There are failed or cancelled runs in history. Use the watchlist to jump into the latest detail pages and inspect failure context.",
    };
  }

  return {
    title: "The queue looks healthy",
    copy: "The current history is clean. If you want a deeper check, move from demo workflows to the provider-backed path next.",
  };
}
