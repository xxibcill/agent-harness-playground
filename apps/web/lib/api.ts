import type {
  CancelRunResponse,
  CreateRunRequest,
  CreateRunResponse,
  ListRunsResponse,
  RunEvent,
  RunRecord,
} from "./generated/contracts";

const DEFAULT_API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

function apiBaseUrl(): string {
  return DEFAULT_API_BASE_URL.replace(/\/$/, "");
}

async function readErrorMessage(response: Response): Promise<string> {
  const fallbackMessage = `Request failed with status ${response.status}.`;

  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? fallbackMessage;
  } catch {
    const text = await response.text();
    return text || fallbackMessage;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  return (await response.json()) as T;
}

export async function listRuns(): Promise<RunRecord[]> {
  const payload = await requestJson<ListRunsResponse>("/runs");
  return payload.runs;
}

export async function fetchRun(runId: string): Promise<RunRecord> {
  const payload = await requestJson<CreateRunResponse>(`/runs/${runId}`);
  return payload.run;
}

export async function createRun(request: CreateRunRequest): Promise<RunRecord> {
  const payload = await requestJson<CreateRunResponse>("/runs", {
    method: "POST",
    body: JSON.stringify(request),
  });
  return payload.run;
}

export async function cancelRun(runId: string): Promise<RunRecord> {
  const payload = await requestJson<CancelRunResponse>(`/runs/${runId}/cancel`, {
    method: "POST",
  });
  return payload.run;
}

export function buildRunEventsStreamUrl(runId: string, sinceSequence = 0, follow = true): string {
  const searchParams = new URLSearchParams({
    follow: String(follow),
    since_sequence: String(sinceSequence),
  });

  return `${apiBaseUrl()}/runs/${runId}/events/stream?${searchParams.toString()}`;
}

function parseEventStreamDocument(document: string): RunEvent[] {
  const events: RunEvent[] = [];

  for (const block of document.split("\n\n")) {
    const dataLines = block
      .split("\n")
      .filter((line) => line.startsWith("data: "))
      .map((line) => line.slice("data: ".length));

    if (dataLines.length === 0) {
      continue;
    }

    events.push(JSON.parse(dataLines.join("\n")) as RunEvent);
  }

  return events;
}

export async function fetchRunEvents(runId: string): Promise<RunEvent[]> {
  const response = await fetch(buildRunEventsStreamUrl(runId, 0, false), {
    cache: "no-store",
    headers: {
      Accept: "text/event-stream",
    },
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  return parseEventStreamDocument(await response.text());
}
