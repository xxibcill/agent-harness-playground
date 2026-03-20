import { RunDetailsPage } from "../../../components/run-details-page";

type RunDetailsRouteProps = {
  params: Promise<{
    runId: string;
  }>;
};

export default async function RunDetailsRoute({ params }: RunDetailsRouteProps) {
  const { runId } = await params;
  return <RunDetailsPage runId={runId} />;
}
