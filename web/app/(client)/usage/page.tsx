"use client";
// Plan usage in units, never cost or credits (MT1, D-63), with the CSV download (GET /v1/usage/export).
import { Download } from "lucide-react";
import { useApi } from "@/lib/useApi";
import type { Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { DataTable, type DataColumn } from "@/ui/DataTable";
import { Banner, EmptyState, LoadError, Meter, PageHead, PageSkeleton } from "@/ui/primitives";
import type { Usage as UsageReply, UsageItem } from "@/lib/gen/api.gen";
import { cellText } from "@/lib/render";
import { humanize } from "@/lib/ws-format";

function Usage({ s }: { s: Session }) {
  const usage = useApi<UsageReply>(s.role === "admin" ? "/v1/usage" : null);
  if (s.role !== "admin") return <div className="content"><PageHead title="Usage and plan" /><Banner kind="neutral">Only admins see plan usage.</Banner></div>;
  const columns: DataColumn<UsageItem>[] = [
    { key: "dimension", header: "Allowance", render: (item) => humanize(cellText(item.dimension)) },
    { key: "used", header: "Used", numeric: true, render: (item) => `${item.used} ${cellText(item.unit)}` },
    { key: "limit", header: "Plan limit", numeric: true, render: (item) => item.limit === null ? "Not set" : `${item.limit} ${cellText(item.unit)}` },
    {
      key: "percent",
      header: "Share used",
      render: (item) => item.percent === null ? <span className="ink-2">Unavailable</span> : (
        <span className="row nowrap"><span className="grow"><Meter value={item.percent} max={100} tone={item.percent >= 100 ? "full" : item.percent >= 80 ? "warn" : undefined} /></span><span className="num">{item.percent}%</span></span>
      ),
    },
  ];
  return (
    <div className="content">
      <PageHead
        title="Usage and plan"
        sub="What this workspace has used this period, in units."
        actions={<a className="btn sm" href="/v1/usage/export" download><Download size={16} aria-hidden="true" />Download CSV</a>}
      />
      {usage.error ? <LoadError reference={null} retryHref="/usage" /> : !usage.data ? <PageSkeleton /> : (
        <div className="stack">
          <p className="t-footnote ink-2">Period {cellText(usage.data.period)}, as of {new Date(usage.data.as_of).toLocaleString()}</p>
          {usage.data.items.length === 0 ? (
            <EmptyState title="No usage recorded" body="Nothing has been used this period yet." />
          ) : (
            <DataTable caption="Workspace usage by allowance" columns={columns} rows={usage.data.items} rowKey={(item) => item.dimension} emptyMessage="No usage items are available." />
          )}
        </div>
      )}
    </div>
  );
}

export default function UsagePage() { return <SignedIn>{(s) => <Usage s={s} />}</SignedIn>; }
