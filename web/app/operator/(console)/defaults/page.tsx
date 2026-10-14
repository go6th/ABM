"use client";
// Platform defaults (A7): what new clients start from (account cap, idle expiry, monthly pool,
// fastest scheduled refresh); the research engine (skill version, credit cap per pull); the
// models users may choose and the default research and analysis models (owner decision
// 2026-10-14); the safeguards that cannot be turned off; and the admins. Save defaults applies to new
// clients; each client can be changed on its own page.
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import type { ConsoleDefaults } from "@/lib/gen/api.gen";
import { useOperatorApi } from "../useOperatorApi";
import { ConsoleLoad } from "../ConsoleLoad";
import { Pill } from "@/ui/design";

type Editable = Pick<ConsoleDefaults, "research_limit_default" | "chat_limit_default" | "limit_reset" | "idle_expiry_days" | "fastest_schedule" | "credit_cap_per_pull" | "default_research_model" | "default_analysis_model" | "max_file_bytes" | "run_dollar_cap_usd" | "repeat_call_limit" | "stall_after_s"> & { enabled_models: string[] };
const GB = 1024 ** 3;

function Row({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="row-16 wrap line-top">
      <div className="grow-240"><label htmlFor={id}><b>{label}</b></label>{hint ? <div className="mut sm-text">{hint}</div> : null}</div>
      {children}
    </div>
  );
}

function Editor({ d, reload }: { d: ConsoleDefaults; reload: () => void }) {
  const [v, setV] = useState<Editable>({
    research_limit_default: d.research_limit_default,
    chat_limit_default: d.chat_limit_default,
    limit_reset: d.limit_reset,
    idle_expiry_days: d.idle_expiry_days,
    fastest_schedule: d.fastest_schedule,
    credit_cap_per_pull: d.credit_cap_per_pull,
    enabled_models: d.models.filter((m) => m.enabled).map((m) => m.id),
    default_research_model: d.default_research_model,
    default_analysis_model: d.default_analysis_model,
    max_file_bytes: d.max_file_bytes,
    run_dollar_cap_usd: d.run_dollar_cap_usd,
    repeat_call_limit: d.repeat_call_limit,
    stall_after_s: d.stall_after_s,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const set = <K extends keyof Editable>(k: K, value: Editable[K]) => setV((prev) => ({ ...prev, [k]: value }));
  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await operatorRequest("PUT /operator/v1/console/defaults", { body: v });
      setMessage({ ok: true, text: "Defaults saved. New clients start from them." });
      reload();
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof ApiError && cause.status === 422 ? (cause.detail || "Check the values and save again.") : "The defaults were not saved. Try again." });
    } finally {
      setBusy(false);
    }
  };
  const int = (s: string) => Math.max(0, Math.floor(Number(s) || 0));

  return (
    <div className="pg admin">
      <div className="head"><div className="ttl"><h1>Platform defaults</h1><div className="sub">Applied to new clients. Each client can be changed on its own page.</div></div></div>
      <section className="card pad stack-14" aria-labelledby="new-title">
        <h2 id="new-title" className="card-title">New client defaults</h2>
        <Row id="researches" label="Researches per user" hint="A full public research of one account: new research, Refresh from web or a scheduled refresh."><input id="researches" className="input sm" type="number" min={0} max={100000} value={v.research_limit_default} onChange={(e) => set("research_limit_default", int(e.target.value))} /></Row>
        <Row id="chats" label="Chats per user" hint="A question in Ask or a file analysis."><input id="chats" className="input sm" type="number" min={0} max={1000000} value={v.chat_limit_default} onChange={(e) => set("chat_limit_default", int(e.target.value))} /></Row>
        <Row id="reset" label="Limits reset" hint="Monthly on the 1st, or never (raise limits by hand).">
          <select id="reset" className="input sm" value={v.limit_reset} onChange={(e) => set("limit_reset", e.target.value === "never" ? "never" : "monthly")}>
            <option value="monthly">Monthly</option><option value="never">Never</option>
          </select>
        </Row>
        <Row id="idle" label="Idle expiry" hint="Warning is sent 3 days before access ends.">
          <select id="idle" className="input sm" value={v.idle_expiry_days} onChange={(e) => set("idle_expiry_days", Number(e.target.value))}>
            {[7, 10, 14, 30].map((n) => <option key={n} value={n}>{n} days</option>)}
          </select>
        </Row>
        <Row id="fastest" label="Fastest scheduled refresh">
          <select id="fastest" className="input sm" value={v.fastest_schedule} onChange={(e) => set("fastest_schedule", e.target.value)}>
            <option value="3d">Every 3 days</option><option value="weekly">Weekly</option><option value="2w">Every 2 weeks</option>
          </select>
        </Row>
      </section>
      <section className="card pad stack-14" aria-labelledby="engine-title">
        <h2 id="engine-title" className="card-title">Research engine</h2>
        <div className="row-16 wrap line-top">
          <div className="grow-240"><b>Research skill version</b><div className="mut sm-text">Updates arrive through the code repository and apply to every client&apos;s next run.</div></div>
          <span>v{d.skill_version} (current){d.skill_installed ? "" : " · skill package not installed yet"}</span>
        </div>
        <Row id="pull" label="Credit cap per pull" hint="A run stops before spending past this. Reported at the end."><input id="pull" className="input sm" type="number" min={1} max={100000} value={v.credit_cap_per_pull} onChange={(e) => set("credit_cap_per_pull", int(e.target.value))} /></Row>
        <Row id="dollar-cap" label="Dollar cap per run" hint="Provider and model spend together. A run that reaches it stops and keeps everything; its owner can resume it.">
          <span className="row-12">
            <span className="mut sm-text">$</span>
            <input id="dollar-cap" className="input sm" type="number" min={0.1} max={10000} step={0.5} value={v.run_dollar_cap_usd} onChange={(e) => set("run_dollar_cap_usd", Math.min(10000, Math.max(0.1, Number(e.target.value) || 0.1)))} />
          </span>
        </Row>
        <Row id="repeats" label="Same search paid at most" hint="Times one run may pay for the same search. One more stops the run."><input id="repeats" className="input sm" type="number" min={1} max={50} value={v.repeat_call_limit} onChange={(e) => set("repeat_call_limit", Math.min(50, Math.max(1, int(e.target.value))))} /></Row>
        <Row id="stall" label="Stalled after" hint="A run with no progress this long is marked stalled; its owner is told and can resume it.">
          <span className="row-12">
            <input id="stall" className="input sm" type="number" min={1} max={1440} value={Math.round(v.stall_after_s / 60)} onChange={(e) => set("stall_after_s", Math.min(1440, Math.max(1, int(e.target.value))) * 60)} />
            <span className="mut sm-text">minutes</span>
          </span>
        </Row>
      </section>
      <section className="card pad stack-14" aria-labelledby="files-title">
        <h2 id="files-title" className="card-title">Files</h2>
        <Row id="max-file" label="Largest file a user may add" hint="Any type. Files go straight to storage and are read in parts. No storage quota.">
          <span className="row-12">
            <input id="max-file" className="input sm" type="number" min={1} max={5120} step={1} value={Math.round(v.max_file_bytes / GB)} onChange={(e) => set("max_file_bytes", Math.min(5120, Math.max(1, int(e.target.value))) * GB)} />
            <span className="mut sm-text">GB</span>
          </span>
        </Row>
      </section>
      <section className="card pad stack-14" aria-labelledby="models-title">
        <h2 id="models-title" className="card-title">Models</h2>
        <p className="mut sm-text">Users choose a research model (pulling and extraction) and an analysis model (answers, file analysis and the deliverables). A model a client may not use is greyed out for its users.</p>
        {d.models.map((m) => (
          <div key={m.id} className="row-16 wrap line-top">
            <label className="check grow-240">
              <input
                type="checkbox"
                checked={v.enabled_models.includes(m.id)}
                onChange={(e) => set("enabled_models", e.target.checked ? [...v.enabled_models, m.id] : v.enabled_models.filter((x) => x !== m.id))}
              />
              <span><b>{m.name}</b><span className="mut sm-text line-below">{m.line}{m.needs_client_permission ? ". Each client must be allowed it on its own page." : ""}</span></span>
            </label>
            {m.set_up ? <Pill tone="ok" small>Set up</Pill> : <Pill tone="neutral" small>Not set up</Pill>}
          </div>
        ))}
        {(["default_research_model", "default_analysis_model"] as const).map((key) => (
          <Row key={key} id={key} label={key === "default_research_model" ? "Default research model" : "Default analysis model"}>
            <select id={key} className="input sm" value={v[key]} onChange={(e) => set(key, e.target.value)}>
              {d.models.filter((m) => v.enabled_models.includes(m.id)).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Row>
        ))}
      </section>
      <section className="card pad stack-14" aria-labelledby="safe-title">
        <h2 id="safe-title" className="card-title">Safeguards</h2>
        <div className="row-16 wrap line-top"><div className="grow-240"><b>Nightly isolation check</b><div className="mut sm-text">Runs at 02:00 and flags any cross-client read.</div></div><Pill tone="ok" small>On, cannot be turned off</Pill></div>
        <div className="row-16 wrap line-top"><b className="grow-240">Client-ready check before publishing library content</b><Pill tone="ok" small>On, cannot be turned off</Pill></div>
        <div className="row-16 wrap line-top"><div className="grow-240"><b>Admin access to client questions</b><div className="mut sm-text">Admins see counts and categories only.</div></div><Pill tone="neutral" small>Off</Pill></div>
      </section>
      <section className="card pad stack-12" aria-labelledby="admins-title">
        <h2 id="admins-title" className="card-title">Admins</h2>
        {d.admins.length ? d.admins.map((a) => <div key={a.email} className="lrow"><span className="grow">{a.email}</span><span className="mut">{a.role}</span></div>) : <p className="mut sm-text">No admins are listed. They are set in the staff sign-in settings.</p>}
        <p className="mut xs-text">Admins are set in the staff sign-in settings (STAFF_OPERATOR_EMAILS and STAFF_SUPPORT_EMAILS).</p>
      </section>
      {message ? <p className={`sm-text ${message.ok ? "mut" : "bad-text"}`} role={message.ok ? "status" : "alert"}>{message.text}</p> : null}
      <div className="row-12 wrap">
        <button type="button" className="btn primary" disabled={busy} onClick={() => void save()}>{busy ? "Saving" : "Save defaults"}</button>
        <span className="mut xs-text">Last changed by {d.updated_by}.</span>
      </div>
    </div>
  );
}

export default function Defaults() {
  const data = useOperatorApi("GET /operator/v1/console/defaults");
  if (!data.data) return <ConsoleLoad title="Platform defaults" state={data} />;
  return <Editor key={data.data.updated_at} d={data.data} reload={data.reload} />;
}
