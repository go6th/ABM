"use client";
// Overview (A1): the last 30 days. The isolation check pill, KPI cards, questions per week with a
// client filter, what people ask about (categories only), and clients not using it.
import Link from "next/link";
import { useState } from "react";
import { useOperatorApi } from "./useOperatorApi";
import { ConsoleLoad } from "./ConsoleLoad";
import { Bar, Pill } from "@/ui/design";
import { expiresIn, isolationPill, num } from "@/lib/console";

function Weekly({ weeks }: { weeks: { week: string; questions: number }[] }) {
  const max = Math.max(1, ...weeks.map((w) => w.questions));
  const label = `Questions per week: ${weeks.map((w) => w.questions).join(", ")} over the last ${weeks.length} weeks`;
  return (
    <svg className="chart" viewBox="0 0 560 190" width="100%" role="img" aria-label={label}>
      <line className="axis" x1="40" y1="160" x2="550" y2="160" />
      {weeks.map((w, i) => {
        const h = Math.round((w.questions / max) * 110);
        const x = 70 + i * 100;
        return (
          <g key={w.week}>
            <rect className="col" x={x} y={160 - h} width="60" height={Math.max(h, 1)} rx="6" />
            <text className="val" x={x + 30} y={152 - h} textAnchor="middle">{num(w.questions)}</text>
            <text className="tick" x={x + 30} y="180" textAnchor="middle">Wk {i + 1}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function Overview() {
  const [client, setClient] = useState("");
  const data = useOperatorApi("GET /operator/v1/console/overview", client ? { query: { client } } : {});
  const o = data.data;
  if (!o) return <ConsoleLoad title="Overview" state={data} />;
  const pill = isolationPill(o.isolation);
  const share = o.questions ? Math.round((o.answered_locally / o.questions) * 100) : 0;
  const totalCats = o.categories.reduce((n, c) => n + c.questions, 0);
  return (
    <div className="pg admin">
      <div className="head">
        <div className="ttl"><h1>Overview</h1><div className="sub">Last 30 days</div></div>
        <Pill tone={pill.tone} big>{pill.text}</Pill>
      </div>
      <div className="kpis">
        <div className="card kpi"><span className="mut sm-text">Active clients</span><span className="v">{num(o.active_clients)}</span><span className="mut xs-text">of {num(o.clients)} onboarded</span></div>
        <div className="card kpi"><span className="mut sm-text">Active users</span><span className="v">{num(o.active_users)}</span><span className="mut xs-text">of {num(o.users)} invited</span></div>
        <div className="card kpi"><span className="mut sm-text">Questions asked</span><span className="v">{num(o.questions)}</span><span className="mut xs-text">{o.questions ? `${share}% answered locally` : "None yet"}</span></div>
        <div className="card kpi"><span className="mut sm-text">Credits used</span><span className="v">{num(o.credits_used)}</span><span className="mut xs-text">of {num(o.credit_pool)} pooled</span></div>
      </div>
      <div className="row-16 wrap align-stretch">
        <section className="card pad stack-14 grow-main" aria-labelledby="weekly-title">
          <div className="row-space">
            <h2 id="weekly-title" className="card-title">Questions per week</h2>
            <label className="sm-text mut">
              <span className="sr-only">Client</span>
              <select className="input sm" value={client} onChange={(e) => setClient(e.target.value)}>
                <option value="">All clients</option>
                {o.client_names.map((c) => <option key={c.tenant_id} value={c.tenant_id}>{c.name}</option>)}
              </select>
            </label>
          </div>
          <Weekly weeks={o.weekly} />
        </section>
        <section className="card pad stack-12 grow-280" aria-labelledby="cats-title">
          <h2 id="cats-title" className="card-title">What people ask about</h2>
          <div className="mut xs-text">Categories only. Question text never leaves the client workspace.</div>
          {o.categories.length ? (
            <div className="breakdown">
              {o.categories.map((c) => {
                const pct = totalCats ? Math.round((c.questions / totalCats) * 100) : 0;
                return (
                  <div key={c.key}>
                    <div className="row-space"><span>{c.label}</span><b>{pct}%</b></div>
                    <Bar value={pct} max={100} height={8} label={`${c.label}: ${pct}%`} />
                  </div>
                );
              })}
            </div>
          ) : <p className="mut sm-text">No questions in the last 30 days.</p>}
        </section>
      </div>
      <section className="card list" aria-labelledby="idle-title">
        <div className="row-space lrow"><h2 id="idle-title" className="card-title">Not using it</h2><Link className="link-strong sm-text" href="/operator/users">All users</Link></div>
        {o.not_using.length ? o.not_using.map((c) => (
          <div key={c.tenant_id} className="lrow">
            <Link className="grow link-strong" href={`/operator/clients/${c.tenant_id}`}>{c.name} · {c.users} user{c.users === 1 ? "" : "s"}</Link>
            <span className="mut">{c.idle_days === null ? "Never active" : `Idle ${c.idle_days} day${c.idle_days === 1 ? "" : "s"}`}</span>
            {c.expires_in_days !== null ? <Pill tone={c.expires_in_days <= 3 ? "warn" : "neutral"} small>{expiresIn(c.expires_in_days)}</Pill> : null}
          </div>
        )) : <div className="lrow mut">Every client used it in the last 3 days.</div>}
      </section>
    </div>
  );
}
