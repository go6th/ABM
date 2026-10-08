import { OperatorNotConnected } from "../OperatorNotConnected";

export default function MonitorsPage() {
  return <OperatorNotConnected title="Platform monitors" detail="The backend exposes readiness checks, but no authenticated operator health, alert freshness, SLO or telemetry endpoint. A readiness probe is not used as a substitute for those signals." />;
}
