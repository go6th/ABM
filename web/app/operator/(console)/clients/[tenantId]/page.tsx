"use client";
// Client detail (A3): limits per user (account cap, idle expiry, monthly credit pool), the 6th
// Library accounts published to this client, features (mail and drive, web refresh by users,
// fastest schedule), and THIS CLIENT NOW with Save changes, See users and the isolation status.
// While the invited user has not accepted, a new invite link can be made here.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import type { ClientInvite, ConsoleClientDetail, ConsoleClientSettings } from "@/lib/gen/api.gen";
import { useOperatorApi } from "../../useOperatorApi";
import { ConsoleLoad } from "../../ConsoleLoad";
import { Pill } from "@/ui/design";
import { isolationPill, lastActive, num } from "@/lib/console";

const IDLE = [7, 10, 14, 30] as const;
const SCHEDULES = [["3d", "Every 3 days"], ["weekly", "Weekly"], ["2w", "Every 2 weeks"], ["monthly", "Monthly"]] as const;

function Seg<T extends string | number | boolean>({ label, value, options, onChange }: { label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>{text}</button>
      ))}
    </div>
  );
}

function Editor({ detail, reload }: { detail: ConsoleClientDetail; reload: () => void }) {
  const [s, setS] = useState<ConsoleClientSettings>(detail.settings);
  const [lib, setLib] = useState<Set<string>>(new Set(detail.library.filter((a) => a.published).map((a) => a.library_account_id)));
  const [allowed, setAllowed] = useState<string[]>(detail.allowed_models);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [invite, setInvite] = useState<ClientInvite | null>(null);
  const c = detail.client;
  const set = <K extends keyof ConsoleClientSettings>(k: K, v: ConsoleClientSettings[K]) => setS((prev) => ({ ...prev, [k]: v }));
  const near = detail.user_idle_days.filter((d) => d >= s.idle_expiry_days - 3).length;
  const pill = isolationPill(detail.isolation);

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await operatorRequest("PUT /operator/v1/console/clients/{tenant_id}", { params: { tenant_id: c.tenant_id }, body: { ...s, library_account_ids: [...lib], allowed_models: allowed } });
      setMessage({ ok: true, text: "Changes saved." });
      reload();
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof ApiError && cause.detail ? cause.detail : "The changes were not saved. Try again." });
    } finally {
      setBusy(false);
    }
  };
  const renew = async () => {
    setBusy(true);
    try {
      setInvite(await operatorRequest("POST /operator/v1/console/clients/{tenant_id}/invite", { params: { tenant_id: c.tenant_id } }));
    } catch {
      setMessage({ ok: false, text: "A new invite link could not be made. Try again." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="split admin">
      <div className="primary-col">
        <div className="head">
          <div className="ttl">
            <Link className="back" href="/operator/clients">‹ Clients</Link>
            <h1>{c.name}</h1>
            <div className="sub">{c.users} user{c.users === 1 ? "" : "s"} · {num(c.accounts)} accounts · {c.last_active_at ? `active ${lastActive(c.last_active_at).toLowerCase()}` : "not active yet"}</div>
          </div>
        </div>
        {detail.pending_invites.length ? (
          <div className="notice info" role="status">
            <p>Invite not accepted yet: {detail.pending_invites.join(", ")}.</p>
            {invite ? <><div className="invite-link">{invite.invite_url}</div><p className="xs-text mut">{invite.emailed ? `Emailed to ${invite.email}. You can also copy it and send it yourself.` : "Copy it and send it yourself (no email was sent)."} It works once and expires in 24 hours.</p></> : (
              <button type="button" className="btn sm align-start" disabled={busy} onClick={() => void renew()}>New invite link</button>
            )}
          </div>
        ) : null}
        <section className="card pad stack-20" aria-labelledby="limits-title">
          <h2 id="limits-title" className="card-title">Limits per user</h2>
          <div className="row-16 wrap">
            <div className="grow-240"><b>Researches per user</b><div className="mut sm-text">A full public research of one account: new research, Refresh from web or a scheduled refresh.</div></div>
            <div className="stepper">
              <button type="button" className="st" aria-label="Fewer researches" disabled={s.research_limit_default <= 0} onClick={() => set("research_limit_default", s.research_limit_default - 1)}>−</button>
              <output aria-live="polite">{s.research_limit_default}</output>
              <button type="button" className="st" aria-label="More researches" onClick={() => set("research_limit_default", s.research_limit_default + 1)}>+</button>
            </div>
          </div>
          <div className="row-16 wrap line-top">
            <div className="grow-240"><label htmlFor="chat-limit"><b>Chats per user</b></label><div className="mut sm-text">A question in Ask or a file analysis.</div></div>
            <input id="chat-limit" className="input sm num-input" type="number" min={0} max={1000000} value={s.chat_limit_default} onChange={(e) => set("chat_limit_default", Math.max(0, Math.floor(Number(e.target.value) || 0)))} />
          </div>
          <div className="row-16 wrap line-top">
            <div className="grow-240"><b>Limits reset</b><div className="mut sm-text">Monthly on the 1st, or never (raise limits by hand).</div></div>
            <Seg label="Limits reset" value={s.limit_reset} options={[["monthly", "Monthly"], ["never", "Never"]] as const} onChange={(v) => set("limit_reset", v)} />
          </div>
          <div className="row-16 wrap line-top">
            <div className="grow-240"><b>Expire access after idle days</b><div className="mut sm-text">Warned 3 days before. Terminated users keep their history. One click to re-enable.</div></div>
            <Seg label="Idle days" value={s.idle_expiry_days} options={IDLE.map((d) => [d, `${d} days`] as const)} onChange={(v) => set("idle_expiry_days", v)} />
          </div>
        </section>
        <section className="card pad stack-14" aria-labelledby="lib-title">
          <div><h2 id="lib-title" className="card-title">Publish from the 6th Library</h2><div className="mut sm-text">Chosen accounts appear in this client only. Other clients never see them.</div></div>
          {detail.library.length ? (
            <div className="lib-grid">
              {detail.library.map((a) => {
                const on = lib.has(a.library_account_id);
                return (
                  <button key={a.library_account_id} type="button" className="opt r12" aria-pressed={on} onClick={() => setLib((prev) => { const next = new Set(prev); if (on) next.delete(a.library_account_id); else next.add(a.library_account_id); return next; })}>
                    <span className="in tight"><span aria-hidden="true">{on ? "✓" : ""}</span><span className="grow">{a.name}<span className="sub mut xs-text">{a.domain}</span></span></span>
                  </button>
                );
              })}
            </div>
          ) : <p className="mut sm-text">No library accounts have passed the client-ready check yet. <Link className="link-strong" href="/operator/library">Open the 6th Library</Link>.</p>}
        </section>
        <section className="card pad stack-20" aria-labelledby="features-title">
          <h2 id="features-title" className="card-title">Features</h2>
          <div className="row-16 wrap">
            <b className="grow-240">Mail and drive connections</b>
            <Seg label="Mail and drive connections" value={s.mail_drive_allowed} options={[[true, "Allowed"], [false, "Not allowed"]] as const} onChange={(v) => set("mail_drive_allowed", v)} />
          </div>
          <div className="row-16 wrap line-top">
            <b className="grow-240">Web refresh by users</b>
            <Seg label="Web refresh by users" value={s.web_refresh_allowed} options={[[true, "Allowed, with confirmation"], [false, "Not allowed"]] as const} onChange={(v) => set("web_refresh_allowed", v)} />
          </div>
          <div className="row-16 wrap line-top">
            <b className="grow-240">Fastest allowed schedule</b>
            <Seg label="Fastest allowed schedule" value={s.fastest_schedule} options={SCHEDULES} onChange={(v) => set("fastest_schedule", v)} />
          </div>
        </section>
        <section className="card pad stack-14" aria-labelledby="models-title">
          <h2 id="models-title" className="card-title">Allowed models</h2>
          <p className="mut sm-text">The models this client&apos;s users may choose. All are on by default; Kimi K3 sends the client&apos;s data to the Kimi provider (Ollama Cloud on staging), so turn it off here if this client must keep data with Claude only.</p>
          {detail.models.map((m) => (
            <label key={m.id} className="check line-top">
              <input
                type="checkbox"
                checked={allowed.includes(m.id)}
                disabled={!m.enabled}
                onChange={(e) => setAllowed(e.target.checked ? [...allowed, m.id] : allowed.filter((x) => x !== m.id))}
              />
              <span><b>{m.name}</b><span className="mut sm-text line-below">{!m.enabled ? "Not enabled on the platform" : m.set_up ? m.line : `${m.line}. Not set up yet`}</span></span>
            </label>
          ))}
        </section>
      </div>
      <div className="side-col w320">
        <section className="card pad stack-14" aria-labelledby="now-title">
          <h2 id="now-title" className="lbl">THIS CLIENT NOW</h2>
          <dl className="kv-grid">
            <dt>Researches</dt><dd>{s.research_limit_default} per user{s.limit_reset === "monthly" ? " a month" : ""}</dd>
            <dt>Chats</dt><dd>{num(s.chat_limit_default)} per user{s.limit_reset === "monthly" ? " a month" : ""}</dd>
            <dt>Idle expiry</dt><dd>{s.idle_expiry_days} days</dd>
            <dt>Library accounts</dt><dd>{lib.size} published</dd>
          </dl>
          {near ? <p className="notice warn sm-text">{near} user{near === 1 ? " is" : "s are"} within 3 days of expiring at this setting.</p> : null}
          {message ? <p className={`sm-text ${message.ok ? "mut" : "bad-text"}`} role={message.ok ? "status" : "alert"}>{message.text}</p> : null}
          <button type="button" className="btn primary block" disabled={busy} onClick={() => void save()}>{busy ? "Saving" : "Save changes"}</button>
          <Link className="btn block" href="/operator/users">See users</Link>
          <Pill tone={pill.tone} small>{pill.text}</Pill>
        </section>
      </div>
    </div>
  );
}

export default function ClientDetail() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const data = useOperatorApi("GET /operator/v1/console/clients/{tenant_id}", { params: { tenant_id: tenantId } });
  if (!data.data) {
    if (data.error?.status === 404) return <ConsoleLoad title="Client not found" state={data} />;
    return <ConsoleLoad title="Client" state={data} />;
  }
  return <Editor key={JSON.stringify(data.data.settings) + data.data.library.map((a) => `${a.library_account_id}${a.published}`).join()} detail={data.data} reload={data.reload} />;
}
