import { proxyRequest } from "../../../../../lib/server/api-proxy.ts";

type RouteContext = {
  params: Promise<{
    runId: string;
  }>;
};

export async function POST(request: Request, context: RouteContext): Promise<Response> {
  const { runId } = await context.params;
  return proxyRequest({
    request,
    path: `/runs/${runId}/cancel`,
    requiredRole: "operator",
  });
}
