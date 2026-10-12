"use client";
// CRM write-back (admins, D-77): off by default. Enable, confirm an account's CRM record, preview
// exactly what would be created, request and approve. New records only; nothing is ever updated
// or deleted in your CRM.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { humanize } from "@/lib/ws-format";
import { Badge, Banner, PageHead, Section } from "@/ui/primitives";

interface Write { id: string; kind: string; title: string; events: string[]; created_at: string }
interface Preview { kind: string; record_id: string; title: string; body: string }

function Crm({ s }: { s: Session }) {
  const [crm, setCrm] = useState<"salesforce" | "hubspot">("salesforce");
  const writes = useApi<{ writes: Write[] }>(s.role === "admin" ? `/v1/crm/${crm}/writes` : null);
  const [domain, setDomain] = useState(""); const [record, setRecord] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (s.role !== "admin") return <div className="content"><PageHead title="CRM write-back" /><Banner kind="neutral">Only admins manage CRM write-back.</Banner></div>;
  const report = (err: unknown) => setMsg({ ok: false, text: err instanceof ApiError ? `Refused (${err.type}).` : "Refused." });
  const call = async (method: string, path: string, body?: unknown) => { setMsg(null); try { const out = await api<unknown>(method, path, body); writes.reload(); return out; } catch (err) { report(err); return null; } };
  const rows = writes.data?.writes ?? [];
  return (
    <div className="content">
      <PageHead title="CRM write-back" sub="Off by default. Only new notes, tasks and snapshot records are created from delivered public findings. Nothing in your CRM is updated or deleted." />
      {msg ? <p className={msg.ok ? "inline-status block-gap" : "inline-error block-gap"} role="status">{msg.text}</p> : null}
      <Section title="Connection">
        <div className="card row">
          <div className="field">
            <label htmlFor="crm-kind">CRM</label>
            <select id="crm-kind" aria-label="CRM" className="select" value={crm} onChange={(e) => setCrm(e.target.value === "hubspot" ? "hubspot" : "salesforce")}><option value="salesforce">Salesforce</option><option value="hubspot">HubSpot</option></select>
          </div>
          <div className="row grow end">
            <button type="button" className="btn tinted" onClick={() => void call("POST", `/v1/crm/${crm}/enable`).then((r) => { if (r) setMsg({ ok: true, text: "Enabled." }); })}>Enable</button>
            <button type="button" className="btn danger" onClick={() => void call("POST", `/v1/crm/${crm}/revoke`).then((r) => { if (r) setMsg({ ok: true, text: "Revoked: writes stop now." }); })}>Revoke</button>
          </div>
        </div>
      </Section>
      <Section title="Account mapping" sub="Confirm which CRM record an account writes to, then preview and request a write.">
        <form className="card form" aria-label="Confirm a mapping" onSubmit={(e) => { e.preventDefault(); void call("POST", `/v1/crm/${crm}/mappings`, { domain: domain.trim(), record_id: record.trim() }).then((r) => { if (r) setMsg({ ok: true, text: "Mapping confirmed." }); }); }}>
          <div className="grid-2">
            <div className="field"><label htmlFor="cdomain">Account domain</label><input id="cdomain" className="input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" /></div>
            <div className="field"><label htmlFor="crecord">CRM record id</label><input id="crecord" className="input mono" value={record} onChange={(e) => setRecord(e.target.value)} /></div>
          </div>
          <div className="row">
            <button type="submit" className="btn">Confirm mapping</button>
            <button type="button" className="btn" onClick={() => void call("POST", `/v1/crm/${crm}/preview`, { domain: domain.trim() }).then((r) => setPreview(r as Preview | null))}>Preview</button>
            <button type="button" className="btn primary" onClick={() => void call("POST", `/v1/crm/${crm}/writes`, { domain: domain.trim() }).then((r) => { if (r) setMsg({ ok: true, text: "Requested. Approve it below." }); })}>Request write</button>
          </div>
        </form>
        {preview ? (
          <div className="card stack gap-top">
            <div className="row between"><h3 className="card-title">{cellText(preview.title)}</h3><Badge tone="peri">{humanize(preview.kind)}</Badge></div>
            <p className="pre-line">{cellText(preview.body)}</p>
          </div>
        ) : null}
      </Section>
      <Section title="Writes">
        {rows.length === 0 ? <p className="ink-2">No writes requested yet.</p> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Write</th><th scope="col">Kind</th><th scope="col">Events</th><th scope="col">Action</th></tr></thead>
              <tbody>{rows.map((w) => (
                <tr key={w.id}><td>{cellText(w.title)}</td><td>{humanize(w.kind)}</td><td>{w.events.map(humanize).join(", ")}</td>
                  <td>{w.events.includes("approved") ? <Badge tone="mint" dot>Approved</Badge> : <button type="button" className="btn sm primary" onClick={() => void call("POST", `/v1/crm/${crm}/writes/approve`, { write_ids: [w.id] })}>Approve</button>}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

export default function CrmPage() { return <SignedIn>{(s) => <Crm s={s} />}</SignedIn>; }
