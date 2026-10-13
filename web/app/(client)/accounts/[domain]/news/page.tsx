// News: the account's signals and changes, newest first, each with its grade and date.
import type { History, SignalPage } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { GradeMark, toGrade } from "@/components/ws/GradeMark";
import { EmptyState, LoadError } from "@/ui/primitives";
import { day, humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

export const dynamic = "force-dynamic";

export default async function NewsTab({ params }: { params: Promise<{ domain: string }> }) {
  const domain = decodeURIComponent((await params).domain);
  const here = `/accounts/${encodeURIComponent(domain)}/news`;
  let signals: SignalPage | null = null;
  let history: History | null = null;
  let reference: string | null | undefined;
  try {
    [signals, history] = await Promise.all([serverGet<SignalPage>("/v1/signals", here), serverGet<History>(`/v1/accounts/${encodeURIComponent(domain)}/history`, here)]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  if (reference !== undefined) return <LoadError reference={reference} retryHref={here} />;
  const items = [
    ...(signals?.items ?? []).filter((s) => s.account === domain && !s.sensitive).map((s) => ({ key: s.event_id, title: humanize(s.signal_type), detail: s.fact_ref ? `Fact ${cellText(s.fact_ref)}` : "", grade: toGrade(s.grade), at: s.detected_at })),
    ...(history?.history ?? []).map((h) => ({ key: h.fact_id, title: `${humanize(h.signal_type)}${h.attribute ? `: ${cellText(h.attribute, 120)}` : ""}`, detail: cellText(h.reason, 200), grade: toGrade(h.grade), at: h.expired_at })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  if (!items.length) return <EmptyState title="No news yet" body="Signals and changes appear here after research runs." />;
  return (
    <ul className="rows">
      {items.map((i) => (
        <li key={i.key}>
          <div><p>{i.title}</p>{i.detail ? <span className="sub">{i.detail}</span> : null}</div>
          <div className="end">{i.grade ? <GradeMark grade={i.grade} /> : null} {day(i.at)}</div>
        </li>
      ))}
    </ul>
  );
}
