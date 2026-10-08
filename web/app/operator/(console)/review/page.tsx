import { OperatorNotConnected } from "../OperatorNotConnected";

export default function ReviewPage() {
  return <OperatorNotConnected title="Review queue" detail="The current backend contract has no operator review-queue endpoint or persisted queue model. No sample queue items are shown." />;
}
