import { NextResponse } from "next/server.js";

import {
  authorizeOperatorRequest,
  type OperatorRole,
  type OperatorSession,
} from "./operator-session.ts";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

type ProxyRequestOptions = {
  path: string;
  request: Request;
  requiredRole: OperatorRole;
};

function apiBaseUrl(): string {
  const configuredBaseUrl =
    process.env.AGENT_HARNESS_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

  return configuredBaseUrl.replace(/\/$/, "");
}

function apiToken(): string | null {
  const token = process.env.AGENT_HARNESS_API_TOKEN?.trim() || "";
  return token ? token : null;
}

function buildUpstreamUrl(path: string, request: Request): string {
  const incomingUrl = new URL(request.url);
  const upstreamUrl = new URL(`${apiBaseUrl()}${path}`);
  incomingUrl.searchParams.forEach((value, key) => {
    upstreamUrl.searchParams.append(key, value);
  });
  return upstreamUrl.toString();
}

function buildUpstreamHeaders(request: Request, session: OperatorSession): Headers {
  const headers = new Headers();
  const token = apiToken();

  if (token !== null) {
    headers.set("authorization", `Bearer ${token}`);
  }

  const accept = request.headers.get("accept");
  if (accept) {
    headers.set("accept", accept);
  }

  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }

  headers.set("x-agent-harness-session-subject", session.subject);
  headers.set("x-agent-harness-session-role", session.role);
  headers.set("x-agent-harness-session-source", session.source);

  return headers;
}

function copyResponseHeaders(sourceHeaders: Headers): Headers {
  const headers = new Headers();
  sourceHeaders.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  return headers;
}

async function readRequestBody(request: Request): Promise<string | undefined> {
  if (request.method === "GET" || request.method === "HEAD") {
    return undefined;
  }

  const body = await request.text();
  return body === "" ? undefined : body;
}

function badGatewayResponse(): NextResponse {
  return NextResponse.json({ detail: "Unable to reach the control plane API." }, { status: 502 });
}

export async function proxyRequest(options: ProxyRequestOptions): Promise<Response> {
  const session = authorizeOperatorRequest(options.request, options.requiredRole);
  if (session instanceof NextResponse) {
    return session;
  }

  try {
    const upstreamResponse = await fetch(buildUpstreamUrl(options.path, options.request), {
      method: options.request.method,
      headers: buildUpstreamHeaders(options.request, session),
      body: await readRequestBody(options.request),
      cache: "no-store",
      signal: options.request.signal,
    });

    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: copyResponseHeaders(upstreamResponse.headers),
    });
  } catch {
    return badGatewayResponse();
  }
}
