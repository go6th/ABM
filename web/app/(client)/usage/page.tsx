"use client";
// Plan usage in units, never cost or credits (MT1, D-63).
import { useApi } from "@/lib/useApi";
import type { Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { DataTable, EmptyState, type DataColumn } from "@/design/components";
import type { Usage as UsageReply, UsageItem } from "@/lib/gen/api.gen";
import { cellText } from "@/lib/render";

function Usage({ s }: { s: Session }) {
  const usage = useApi<UsageReply>(s.role === "admin" ? "/v1/usage" : null);
  if (s.role !== "admin") return <p>Only admins see plan usage.</p>;
  const columns: DataColumn<UsageItem>[] = [
    { key: "dimension", header: "Allowance", render: (item) => cellText(item.dimension.replaceAll("_", " ")) },
    { key: "used", header: "Used", render: (item) => `${item.used} ${cellText(item.unit)}` },
    { key: "limit", header: "Plan limit", render: (item) => item.limit === null ? "Not set" : `${item.limit} ${cellText(item.unit)}` },
    { key: "percent", header: "Share used", render: (item) => item.percent === null ? "Unavailable" : `${item.percent}%` },
  ];
  return (
    <section className="client-dashboard">
      <header className="client-dashboard-heading">
        <div><p className="eyebrow">WORKSPACE ADMINISTRATION</p><h1>Usage &amp; plan</h1>
          <p className="secondary">Unit-based tenant usage; provider costs and internal credit details are not displayed.</p></div>
      </header>
      {usage.error ? (
        <div className="client-data-state error-box" role="alert">
          <strong>Usage could not be loaded</strong><p>{usage.error.detail}</p>
          <button className="btn" type="button" onClick={usage.reload}>Try again</button>
        </div>
      ) : !usage.data ? (
        <p className="client-loading" role="status">Loading usage…</p>
      ) : (
        <>
          <p className="muted">Period: {cellText(usage.data.period)} · As of {cellText(usage.data.as_of)}</p>
          {usage.data.items.length === 0 ? (
            <EmptyState title="No usage recorded" description="The service returned no usage items for this period." />
          ) : (
            <DataTable
              caption="Workspace usage by allowance"
              columns={columns}
              rows={usage.data.items}
              rowKey={(item) => item.dimension}
              emptyMessage="No usage items are available."
            />
          )}
        </>
      )}
    </section>
  );
}

export default function UsagePage() { return <SignedIn>{(s) => <Usage s={s} />}</SignedIn>; }
