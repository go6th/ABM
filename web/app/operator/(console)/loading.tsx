import { Skeleton } from "@/ui/design";

export default function ConsoleLoading() {
  return (
    <div className="pg admin" role="status" aria-label="Loading">
      <div className="stack-12" aria-busy="true"><Skeleton kind="title" /><Skeleton kind="block" /><Skeleton kind="row" /><Skeleton kind="row" /></div>
    </div>
  );
}
