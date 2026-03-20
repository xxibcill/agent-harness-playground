import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { GET as getRunDetail } from "../app/api/runs/[runId]/route.ts";
import { POST as cancelRun } from "../app/api/runs/[runId]/cancel/route.ts";
import { GET as streamRunEvents } from "../app/api/runs/[runId]/events/stream/route.ts";
import { GET as listRuns, POST as createRun } from "../app/api/runs/route.ts";

type FetchCall = {
  init?: RequestInit;
  input: RequestInfo | URL;
};

const originalEnv = { ...process.env };
const originalFetch = globalThis.fetch;

let fetchCalls: FetchCall[] = [];

function createTrustedProxyHeaders(overrides?: {
  role?: string;
  secret?: string;
  user?: string;
}): Headers {
  return new Headers({
    accept: "application/json",
    "x-agent-harness-proxy-secret": overrides?.secret ?? "proxy-secret",
    "x-forwarded-role": overrides?.role ?? "operator",
    "x-forwarded-user": overrides?.user ?? "alice@example.com",
  });
}

function installFetchStub(factory: (call: FetchCall) => Response | Promise<Response>): void {
  fetchCalls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { input, init };
    fetchCalls.push(call);
    return factory(call);
  }) as typeof fetch;
}

function setServerEnv(): void {
  process.env.AGENT_HARNESS_API_BASE_URL = "http://127.0.0.1:8000";
  process.env.AGENT_HARNESS_API_TOKEN = "server-token";
  process.env.AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET = "proxy-secret";
  delete process.env.NEXT_PUBLIC_API_BASE_URL;
  delete process.env.NEXT_PUBLIC_API_TOKEN;
  delete process.env.AGENT_HARNESS_WEB_DEV_ROLE;
}

beforeEach(() => {
  Object.assign(process.env, originalEnv);
  setServerEnv();
});

afterEach(() => {
  process.env = { ...originalEnv };
  globalThis.fetch = originalFetch;
  fetchCalls = [];
});

test("list runs forwards the trusted proxy request with server auth", async () => {
  installFetchStub(() =>
    Response.json({
      runs: [{ run_id: "run-123", status: "queued" }],
    }),
  );

  const response = await listRuns(
    new Request("http://localhost:3000/api/runs?limit=10", {
      headers: createTrustedProxyHeaders({ role: "viewer" }),
    }),
  );

  assert.equal(response.status, 200);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].input, "http://127.0.0.1:8000/runs?limit=10");
  assert.equal(fetchCalls[0].init?.method, "GET");

  const forwardedHeaders = fetchCalls[0].init?.headers as Headers;
  assert.equal(forwardedHeaders.get("authorization"), "Bearer server-token");
  assert.equal(forwardedHeaders.get("x-agent-harness-session-subject"), "alice@example.com");
  assert.equal(forwardedHeaders.get("x-agent-harness-session-role"), "viewer");

  const payload = (await response.json()) as { runs: Array<{ run_id: string }> };
  assert.equal(payload.runs[0]?.run_id, "run-123");
});

test("create run is rejected before proxying when the session role is too weak", async () => {
  installFetchStub(() => Response.json({ ok: true }));

  const response = await createRun(
    new Request("http://localhost:3000/api/runs", {
      method: "POST",
      headers: createTrustedProxyHeaders({ role: "viewer" }),
      body: JSON.stringify({ workflow: "demo.echo", input: "hello" }),
    }),
  );

  assert.equal(response.status, 403);
  assert.equal(fetchCalls.length, 0);
  assert.deepEqual(await response.json(), { detail: "operator role required." });
});

test("run detail proxy uses the local development fallback when trusted proxy mode is off", async () => {
  delete process.env.AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET;

  installFetchStub(() =>
    Response.json({
      run: { run_id: "run-456", status: "running" },
    }),
  );

  const response = await getRunDetail(
    new Request("http://localhost:3000/api/runs/run-456"),
    { params: Promise.resolve({ runId: "run-456" }) },
  );

  assert.equal(response.status, 200);
  assert.equal(fetchCalls.length, 1);

  const forwardedHeaders = fetchCalls[0].init?.headers as Headers;
  assert.equal(forwardedHeaders.get("x-agent-harness-session-source"), "development");
  assert.equal(forwardedHeaders.get("x-agent-harness-session-role"), "admin");
});

test("cancel route forwards request bodies and methods with operator auth", async () => {
  installFetchStub(() =>
    Response.json({
      run: { run_id: "run-789", status: "cancelling" },
    }),
  );

  const response = await cancelRun(
    new Request("http://localhost:3000/api/runs/run-789/cancel?reason=manual", {
      method: "POST",
      headers: createTrustedProxyHeaders(),
    }),
    { params: Promise.resolve({ runId: "run-789" }) },
  );

  assert.equal(response.status, 200);
  assert.equal(fetchCalls[0].input, "http://127.0.0.1:8000/runs/run-789/cancel?reason=manual");
  assert.equal(fetchCalls[0].init?.method, "POST");
});

test("event streaming passes through upstream SSE responses unchanged", async () => {
  installFetchStub(() => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue("id: 7\nevent: run-event\ndata: {\"event_id\":\"evt-7\"}\n\n");
        controller.close();
      },
    }).pipeThrough(new TextEncoderStream());

    return new Response(stream, {
      headers: {
        "cache-control": "no-cache",
        connection: "keep-alive",
        "content-type": "text/event-stream; charset=utf-8",
      },
      status: 200,
    });
  });

  const response = await streamRunEvents(
    new Request(
      "http://localhost:3000/api/runs/run-999/events/stream?since_sequence=7&follow=false",
      {
        headers: createTrustedProxyHeaders({ role: "viewer" }),
      },
    ),
    { params: Promise.resolve({ runId: "run-999" }) },
  );

  assert.equal(response.status, 200);
  assert.equal(fetchCalls[0].input, "http://127.0.0.1:8000/runs/run-999/events/stream?since_sequence=7&follow=false");
  assert.equal(response.headers.get("content-type"), "text/event-stream; charset=utf-8");
  assert.equal(response.headers.get("connection"), null);
  assert.equal(
    await response.text(),
    "id: 7\nevent: run-event\ndata: {\"event_id\":\"evt-7\"}\n\n",
  );
});
