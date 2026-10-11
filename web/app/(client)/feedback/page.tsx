"use client";
// Feedback: how often research was used (GET /v1/usefulness/metric) and the feedback people left
// on rows and runs (GET /v1/feedback). Admins and anyone who may read usage.
import { MessageSquareHeart } from "lucide-react";
import { type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { day, humanize } from "@/lib/ws-format";
import { reasonLabel } from "@/ui/Feedback";
import { DataTable, type DataColumn } from "@/ui/DataTable";
import { Badge, Banner, EmptyState, LoadError, PageHead, Section, Tile } from "@/ui/primitives";

interface Rate { base: number; used: number; rate: number | null; small_base: boolean }
interface Metric { status: string; window_days: number; question_use: Rate; confirmed_fact_reference: Rate; feedback: Array<{ verdict: string; reason: string; count: number }> }
interface Item { id: string | number; at: string; domain: string; verdict: string; reason: string; note: string | null; table: string | null; scope: string }

function pct(r: Rate): string {
  return r.rate === null ? "Not enough data" : `${Math.round(r.rate * 100)}%`;
}

function Feedback({ s, caps }: { s: Session; caps: string[] }) {
  const allowed = s.role === "admin" || caps.includes("usage:read");
  const metric = useApi<Metric>(allowed ? "/v1/usefulness/metric" : null);
  const list = useApi<{ feedback: Item[] }>(allowed ? "/v1/feedback" : null);
  if (!allowed) return <div className="content"><PageHead title="Feedback" /><Banner kind="neutral">Only admins see workspace feedback.</Banner></div>;
  const columns: DataColumn<Item>[] = [
    { key: "at", header: "When", render: (i) => <span className="nowrap">{day(i.at)}</span> },
    { key: "account", header: "Account", render: (i) => cellText(i.domain) },
    { key: "where", header: "Where", render: (i) => (i.table ? humanize(i.table) : humanize(i.scope)) },
    { key: "verdict", header: "Verdict", render: (i) => <Badge tone={i.verdict === "useful" ? "mint" : "rose"}>{i.verdict === "useful" ? "Useful" : "Not useful"}</Badge> },
    { key: "reason", header: "Reason", render: (i) => reasonLabel(i.reason) },
    { key: "note", header: "Note", render: (i) => (i.note ? cellText(i.note, 200) : <span className="ink-2">None</span>) },
  ];
  return (
    <div className="content">
      <PageHead title="Feedback" sub="Whether research is being used, and what people said about it." />
      {metric.error ? <LoadError reference={null} retryHref="/feedback" /> : metric.data ? (
        <Section title={`Last ${metric.data.window_days} days`}>
          <div className="grid-2">
            <Tile tone="peri" label="First call questions used" value={pct(metric.data.question_use)} note={`${metric.data.question_use.used} of ${metric.data.question_use.base}${metric.data.question_use.small_base ? ", small sample" : ""}`} />
            <Tile tone="mint" label="Confirmed facts referenced" value={pct(metric.data.confirmed_fact_reference)} note={`${metric.data.confirmed_fact_reference.used} of ${metric.data.confirmed_fact_reference.base}${metric.data.confirmed_fact_reference.small_base ? ", small sample" : ""}`} />
          </div>
        </Section>
      ) : null}
      <Section title="What people said">
        {list.error ? <LoadError reference={null} retryHref="/feedback" /> : list.data && list.data.feedback.length === 0 ? (
          <EmptyState title="No feedback yet" body="Feedback left on table rows and runs appears here." icon={<MessageSquareHeart size={20} strokeWidth={1.8} />} />
        ) : (
          <DataTable caption="Feedback" columns={columns} rows={list.data?.feedback ?? []} rowKey={(i) => String(i.id)} loading={!list.data} />
        )}
      </Section>
    </div>
  );
}

function WithCaps({ s }: { s: Session }) {
  const me = useApi<{ capabilities: string[] }>("/v1/me");
  if (!me.data) return me.error ? <LoadError reference={null} retryHref="/feedback" /> : null;
  return <Feedback s={s} caps={me.data.capabilities} />;
}

export default function FeedbackPage() { return <SignedIn>{(s) => <WithCaps s={s} />}</SignedIn>; }
