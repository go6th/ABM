"use client";
// Alert inbox: the caller's alerts, Leads labelled, with a usefulness rating from a fixed list
// (AL8). Admins also see how useful delivered alerts have been (GET /v1/alerts/usefulness).
import Link from "next/link";
import { useState } from "react";
import { Bell } from "lucide-react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { ago, humanize } from "@/lib/ws-format";
import { reasonLabel } from "@/ui/Feedback";
import { Badge, Banner, EmptyState, LoadError, PageHead, PageSkeleton, Tile } from "@/ui/primitives";

interface Alert { id: string; rule: string; tier: string; lead: boolean; subject: string; body: string; created_at: string }
interface Usefulness { delivered: number; rated: number; useful: number; share: number | null; small_base: boolean; target: number; target_is_a_result: boolean }

const REASONS: Record<"useful" | "not_useful", string[]> = {
  useful: ["acted_on", "new_to_me", "good_timing"],
  not_useful: ["already_known", "wrong", "not_relevant", "too_late", "too_many"],
};

function Rate({ alert }: { alert: Alert }) {
  const [verdict, setVerdict] = useState<"useful" | "not_useful">("useful");
  const [reason, setReason] = useState(REASONS.useful[0]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    setMsg(null);
    try { await api("POST", `/v1/alerts/${encodeURIComponent(alert.id)}/feedback`, { verdict, reason }); setMsg({ ok: true, text: "Feedback saved." }); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? `Feedback was refused (${err.type}).` : "Feedback was refused." }); }
    finally { setBusy(false); }
  };
  return (
    <div className="row" role="group" aria-label={`Rate alert ${alert.subject}`}>
      <div className="seg" role="group" aria-label="Verdict">
        {(["useful", "not_useful"] as const).map((v) => (
          <button key={v} type="button" aria-pressed={verdict === v} disabled={busy} onClick={() => { setVerdict(v); setReason(REASONS[v][0]); }}>
            {v === "useful" ? "Useful" : "Not useful"}
          </button>
        ))}
      </div>
      <select aria-label="Reason" className="select" disabled={busy} value={reason} onChange={(e) => setReason(e.target.value)}>
        {REASONS[verdict].map((r) => <option key={r} value={r}>{reasonLabel(r)}</option>)}
      </select>
      <button className="btn sm tinted" type="button" disabled={busy} onClick={() => void send()}>{busy ? "Saving" : "Rate"}</button>
      {msg ? <span className={msg.ok ? "inline-status" : "inline-error"} role="status">{msg.text}</span> : null}
    </div>
  );
}

function UsefulnessSummary() {
  const u = useApi<Usefulness>("/v1/alerts/usefulness");
  if (!u.data) return null;
  const share = u.data.share === null ? "Not enough ratings" : `${Math.round(u.data.share * 100)}%`;
  return (
    <section className="section" aria-labelledby="usefulness">
      <div className="section-head">
        <div>
          <h2 id="usefulness">How useful alerts have been</h2>
          <p className="sub">{u.data.small_base ? "Few alerts have been rated, so the share can move a lot." : `Target ${Math.round(u.data.target * 100)}% rated useful.`}</p>
        </div>
      </div>
      <div className="grid-3">
        <Tile tone="peri" label="Delivered" value={u.data.delivered} />
        <Tile tone="lilac" label="Rated" value={u.data.rated} />
        <Tile tone="mint" label="Rated useful" value={share} note={`${u.data.useful} of ${u.data.rated}`} />
      </div>
    </section>
  );
}

function Inbox({ s }: { s: Session }) {
  const list = useApi<{ alerts: Alert[] }>("/v1/alerts");
  const alerts = list.data?.alerts ?? [];
  return (
    <div className="reading">
      <PageHead
        title="Alerts"
        sub="Signals delivered to you from your accounts."
        actions={<Link className="btn sm" href="/settings/alerts">Alert preferences</Link>}
      />
      {list.error ? <LoadError reference={null} retryHref="/alerts" />
        : !list.data ? <PageSkeleton width="reading" />
          : alerts.length === 0 ? (
            <EmptyState
              title="No alerts yet"
              body="When one of your alert rules fires, the alert appears here."
              icon={<Bell size={20} strokeWidth={1.8} />}
              action={{ href: "/settings/alerts", label: "Review alert preferences" }}
            />
          ) : (
            <ul className="stack" aria-label="Alerts">
              {alerts.map((a) => (
                <li key={a.id} className="card stack">
                  <div className="row between">
                    <h2 className="card-title">{cellText(a.subject)}</h2>
                    <span className="row">
                      {a.lead ? <Badge tone="butter" dot>Lead, not yet verified</Badge> : null}
                      <Badge tone="peri">{humanize(a.tier)}</Badge>
                    </span>
                  </div>
                  {a.lead ? <Banner kind="warn">This is an unverified lead. Review the supporting research before treating it as established.</Banner> : null}
                  <p>{cellText(a.body)}</p>
                  <p className="t-footnote ink-2">{humanize(cellText(a.rule))}, {ago(a.created_at)}</p>
                  <Rate alert={a} />
                </li>
              ))}
            </ul>
          )}
      {s.role === "admin" ? <UsefulnessSummary /> : null}
    </div>
  );
}

export default function AlertsPage() { return <SignedIn>{(s) => <Inbox s={s} />}</SignedIn>; }
