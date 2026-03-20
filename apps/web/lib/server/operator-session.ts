import { NextResponse } from "next/server.js";

export type OperatorRole = "viewer" | "operator" | "admin";

export type OperatorSession = {
  role: OperatorRole;
  source: "development" | "trusted-proxy";
  subject: string;
};

const ROLE_ORDER: Record<OperatorRole, number> = {
  viewer: 1,
  operator: 2,
  admin: 3,
};

type TrustedProxyConfig = {
  roleHeaderName: string;
  secret: string;
  secretHeaderName: string;
  userHeaderName: string;
};

function readTrustedProxyConfig(): TrustedProxyConfig | null {
  const secret = process.env.AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET?.trim() ?? "";
  if (!secret) {
    return null;
  }

  return {
    secret,
    secretHeaderName:
      process.env.AGENT_HARNESS_WEB_TRUSTED_PROXY_SECRET_HEADER?.trim() ||
      "x-agent-harness-proxy-secret",
    userHeaderName:
      process.env.AGENT_HARNESS_WEB_TRUSTED_PROXY_USER_HEADER?.trim() || "x-forwarded-user",
    roleHeaderName:
      process.env.AGENT_HARNESS_WEB_TRUSTED_PROXY_ROLE_HEADER?.trim() || "x-forwarded-role",
  };
}

function parseRole(value: string | null): OperatorRole | null {
  if (value === "viewer" || value === "operator" || value === "admin") {
    return value;
  }
  return null;
}

function hasRequiredRole(actualRole: OperatorRole, requiredRole: OperatorRole): boolean {
  return ROLE_ORDER[actualRole] >= ROLE_ORDER[requiredRole];
}

function unauthorizedResponse(message: string): NextResponse {
  return NextResponse.json({ detail: message }, { status: 401 });
}

function forbiddenResponse(message: string): NextResponse {
  return NextResponse.json({ detail: message }, { status: 403 });
}

function authorizeTrustedProxyRequest(
  request: Request,
  requiredRole: OperatorRole,
  config: TrustedProxyConfig,
): OperatorSession | NextResponse {
  const providedSecret = request.headers.get(config.secretHeaderName)?.trim() ?? "";
  if (providedSecret !== config.secret) {
    return unauthorizedResponse("Missing or invalid trusted proxy secret.");
  }

  const subject = request.headers.get(config.userHeaderName)?.trim() ?? "";
  if (!subject) {
    return unauthorizedResponse("Missing trusted proxy user identity.");
  }

  const role = parseRole(request.headers.get(config.roleHeaderName)?.trim() ?? null);
  if (role === null) {
    return unauthorizedResponse("Missing or invalid trusted proxy role.");
  }

  if (!hasRequiredRole(role, requiredRole)) {
    return forbiddenResponse(`${requiredRole} role required.`);
  }

  return {
    role,
    source: "trusted-proxy",
    subject,
  };
}

function resolveDevelopmentSession(requiredRole: OperatorRole): OperatorSession | NextResponse {
  const role = parseRole(process.env.AGENT_HARNESS_WEB_DEV_ROLE?.trim() ?? null) ?? "admin";
  if (!hasRequiredRole(role, requiredRole)) {
    return forbiddenResponse(`${requiredRole} role required.`);
  }

  return {
    role,
    source: "development",
    subject: "local-operator",
  };
}

export function authorizeOperatorRequest(
  request: Request,
  requiredRole: OperatorRole,
): OperatorSession | NextResponse {
  const trustedProxyConfig = readTrustedProxyConfig();
  if (trustedProxyConfig !== null) {
    return authorizeTrustedProxyRequest(request, requiredRole, trustedProxyConfig);
  }
  return resolveDevelopmentSession(requiredRole);
}
