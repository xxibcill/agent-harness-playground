import { proxyRequest } from "../../../lib/server/api-proxy.ts";

export async function GET(request: Request): Promise<Response> {
  return proxyRequest({
    request,
    path: "/runs",
    requiredRole: "viewer",
  });
}

export async function POST(request: Request): Promise<Response> {
  return proxyRequest({
    request,
    path: "/runs",
    requiredRole: "operator",
  });
}
