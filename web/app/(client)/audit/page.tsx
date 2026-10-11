"use client";
// The workspace audit trail (admins): reads, runs, exports, administration and support access,
// identifiers only. The CSV export shows categories, never providers, credits or cost.
import Link from "next/link";
import { Download } from "lucide-react";
import { type Session } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { SignedIn } from "@/ui/SignedIn";
import { DataTable, type DataColumn } from "@/ui/DataTable";
import { Badge, Banner, LoadError, PageHead } from "@/ui/primitives";
import { cellText } from "@/lib/render";

interface Ev { seq: number; at: string; actor: string; actor_kind: string; action: string; resource_type: string; resource_id: string | null; outcome: string }

function Audit({ s }: { s: Session }) {
  const loaded = useApi<{ events: Ev[] }>(s.role === "admin" ? "/v1/audit?limit=100" : null);
  if (s.role !== "admin") return <div className="content"><PageHead title="Audit trail" /><Banner kind="neutral">Only admins read the audit trail.</Banner></div>;
  const columns: DataColumn<Ev>[] = [
    { key: "seq", header: "Number", numeric: true, render: (e) => e.seq },
    { key: "at", header: "When", render: (e) => <span className="nowrap">{new Date(e.at).toLocaleString()}</span> },
    { key: "who", header: "Who", render: (e) => <span className="mono t-footnote">{cellText(e.actor)}{e.actor_kind === "support" ? " (6th support)" : ""}</span> },
    { key: "what", header: "What", render: (e) => <span className="mono t-footnote">{cellText(e.action)}</span> },
    { key: "resource", header: "Resource", render: (e) => <span className="t-footnote">{cellText(e.resource_type)} <span className="mono">{cellText(e.resource_id)}</span></span> },
    { key: "outcome", header: "Outcome", render: (e) => <Badge tone={e.outcome === "ok" || e.outcome === "success" ? "mint" : "rose"}>{cellText(e.outcome)}</Badge> },
  ];
  return (
    <div className="wide">
      <PageHead
        title="Audit trail"
        sub="Everything done in this workspace, by whom and with what result. The latest 100 events."
        actions={<Link className="btn sm" href="/v1/audit/export" prefetch={false}><Download size={16} aria-hidden="true" />Download the audit trail (CSV)</Link>}
      />
      {loaded.error ? <LoadError reference={null} retryHref="/audit" /> : (
        <DataTable caption="Audit events" columns={columns} rows={loaded.data?.events ?? []} rowKey={(e) => String(e.seq)} loading={!loaded.data} emptyMessage="No events recorded yet." />
      )}
    </div>
  );
}

export default function AuditPage() { return <SignedIn>{(s) => <Audit s={s} />}</SignedIn>; }
