"use client";
// Runs and spend (A6): credits used this month by purpose, the nightly isolation check, and every
// research run and refresh across clients (newest first).
import { Fragment } from "react";
import { useOperatorApi } from "../useOperatorApi";
import { ConsoleLoad } from "../ConsoleLoad";
import { Pill } from "@/ui/design";
import { isolationPill, num, runTone, started } from "@/lib/console";

export default function Runs() {
  const data = useOperatorApi("GET /operator/v1/console/runs");
  if (!data.data) return <ConsoleLoad title="Runs and spend" state={data} />;
  const d = data.data;
  const pill = isolationPill(d.isolation);
  return (
    <div className="pg admin">
      <div className="head"><div className="ttl"><h1>Runs and spend</h1><div className="sub">Every research run and refresh, across clients</div></div></div>
      <div className="row-16 wrap align-stretch">
        <section className="card pad stack-12 grow-main" aria-labelledby="purpose-title">
          <h2 id="purpose-title" className="card-title">Credits by purpose</h2>
          <dl className="kv-grid">
            {d.by_purpose.map((p) => <Fragment key={p.key}><dt>{p.label}</dt><dd>{num(p.credits)}</dd></Fragment>)}
          </dl>
          <p className="mut xs-text">This month, credits used by finished jobs.</p>
        </section>
        <section className="card pad stack-12 grow-280" aria-labelledby="iso-title">
          <h2 id="iso-title" className="card-title">Isolation check</h2>
          <Pill tone={pill.tone} small>{pill.text.replace("Isolation check ", "").replace(/^./, (c) => c.toUpperCase())}</Pill>
          <p className="mut sm-text">Nightly test that no client can read, search or cite another client&apos;s accounts, files or answers. {d.isolation.status === "not_run" ? "It has not run yet." : `${d.isolation.workspaces_checked} of ${d.workspaces} workspaces checked.`}</p>
        </section>
      </div>
      <div className="card tscroll">
        <div className="trow th cols-runs"><span>Client</span><span>Account</span><span>Type</span><span>Credits</span><span>Started</span><span>Status</span></div>
        {d.runs.map((r) => (
          <div key={r.job_id} className="trow cols-runs">
            <span>{r.client}</span>
            <b>{r.account}</b>
            <span>{r.type}</span>
            <span>{num(r.credits)}</span>
            <span>{started(r.started_at)}</span>
            <span><Pill tone={runTone(r.status)} small>{r.status}</Pill></span>
          </div>
        ))}
        {!d.runs.length ? <div className="state"><h2>No runs yet</h2><p>New research, scheduled refreshes and web refreshes appear here.</p></div> : null}
      </div>
    </div>
  );
}
