import { OperatorNotConnected } from "../OperatorNotConnected";

export default function AuditPage() {
  return <OperatorNotConnected title="Operator audit explorer" detail="Operator actions are written to the backend audit store, but the current API contract has no read endpoint for those records. Audit rows are not fabricated or fetched through a tenant session." />;
}
