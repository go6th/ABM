"use client";
// Run detail (A6): one client's run with the same token card the client sees: tokens per step,
// model cost in dollars and how the run compares with what such a research usually takes.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useOperatorApi } from "../../../useOperatorApi";
import { ConsoleLoad } from "../../../ConsoleLoad";
import { Pill } from "@/ui/design";
import { runTone } from "@/lib/console";
import { TokenMeter } from "@/components/ws/TokenMeter";

const STATUS: Record<string, string> = {
  running: "Running", queued: "Queued", succeeded: "Done", partial: "Done with gaps",
  failed: "Failed", canceled: "Stopped", cancelled: "Stopped", awaiting_approval: "Waiting for approval",
};

export default function RunDetail() {
  const { tenantId, runId } = useParams<{ tenantId: string; runId: string }>();
  const data = useOperatorApi("GET /operator/v1/console/runs/{tenant_id}/{run_id}", { params: { tenant_id: tenantId, run_id: runId } });
  if (!data.data) {
    if (data.error?.status === 404) return <ConsoleLoad title="Run not found" state={data} />;
    return <ConsoleLoad title="Run" state={data} />;
  }
  const d = data.data;
  const label = STATUS[d.status.toLowerCase()] ?? d.status;
  return (
    <div className="pg admin">
      <div className="head">
        <div className="ttl">
          <Link className="back" href="/operator/runs">‹ Runs and spend</Link>
          <h1>{d.client}</h1>
          <div className="sub">Run {d.run_id.slice(0, 8)}{d.finished_reason ? ` · ${d.finished_reason.replace(/_/g, " ")}` : ""}</div>
        </div>
        <Pill tone={runTone(label)} small>{label}</Pill>
      </div>
      <TokenMeter run={{ token_use: d.token_use }} status={d.status.toLowerCase()} />
      <div className="row-12 wrap">
        <Link className="btn" href={`/operator/clients/${d.tenant_id}`}>Open client</Link>
        <button type="button" className="btn" onClick={data.reload}>Refresh</button>
      </div>
    </div>
  );
}
