"use client";
// CRM write-back (admins, D-77): off by default. Enable, confirm an account's CRM record, preview exactly what would be created, request and approve. New records only; nothing is ever updated or deleted in your CRM.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";

interface Write { id: string; kind: string; title: string; events: string[]; created_at: string }
interface Preview { kind: string; record_id: string; title: string; body: string }

function Crm({ s }: { s: Session }) {
  const [crm, setCrm] = useState<"salesforce" | "hubspot">("salesforce");
  const writes = useApi<{ writes: Write[] }>(s.role === "admin" ? `/v1/crm/${crm}/writes` : null);
  const [domain, setDomain] = useState(""); const [record, setRecord] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (s.role !== "admin") return <p>Only admins manage CRM write-back.</p>;
  const report = (err: unknown) => setMsg(err instanceof ApiError ? `Refused (${err.type}).` : "Refused.");
  const call = async (method: string, path: string, body?: unknown) => { try { const out = await api<unknown>(method, path, body); writes.reload(); return out; } catch (err) { report(err); return null; } };
  return (
    <section>
      <h1>CRM write-back</h1>
      <p className="muted">Off by default. Only new notes, tasks and snapshot records are created from delivered public findings; nothing in your CRM is ever updated or deleted.</p>
      <div className="row">
        <select aria-label="CRM" value={crm} onChange={(e) => setCrm(e.target.value === "hubspot" ? "hubspot" : "salesforce")}><option value="salesforce">Salesforce</option><option value="hubspot">HubSpot</option></select>
        <button type="button" onClick={() => void call("POST", `/v1/crm/${crm}/enable`).then((r) => r && setMsg("Enabled."))}>Enable</button>
        <button type="button" onClick={() => void call("POST", `/v1/crm/${crm}/revoke`).then((r) => r && setMsg("Revoked: writes stop now."))}>Revoke</button>
      </div>
      <form className="card" aria-label="Confirm a mapping" onSubmit={(e) => { e.preventDefault(); void call("POST", `/v1/crm/${crm}/mappings`, { domain: domain.trim(), record_id: record.trim() }).then((r) => r && setMsg("Mapping confirmed.")); }}>
        <label htmlFor="cdomain">Account domain</label><input id="cdomain" value={domain} onChange={(e) => setDomain(e.target.value)} />
        <label htmlFor="crecord">CRM record id</label><input id="crecord" value={record} onChange={(e) => setRecord(e.target.value)} />
        <button type="submit">Confirm mapping</button>
      </form>
      <div className="row">
        <button type="button" onClick={() => void call("POST", `/v1/crm/${crm}/preview`, { domain: domain.trim() }).then((r) => setPreview(r as Preview | null))}>Preview</button>
        <button type="button" onClick={() => void call("POST", `/v1/crm/${crm}/writes`, { domain: domain.trim() }).then((r) => r && setMsg("Requested; approve below."))}>Request write</button>
      </div>
      {preview ? <div className="card"><h2>{cellText(preview.title)}</h2><pre>{cellText(preview.body)}</pre></div> : null}
      {msg ? <p role="status">{msg}</p> : null}
      <table className="data"><thead><tr><th scope="col">Write</th><th scope="col">Kind</th><th scope="col">Events</th><th scope="col">Action</th></tr></thead>
        <tbody>{(writes.data?.writes ?? []).map((w) => (
          <tr key={w.id}><td>{cellText(w.title)}</td><td>{w.kind}</td><td>{w.events.join(", ")}</td>
            <td>{w.events.includes("approved") ? <span className="muted">Approved</span> : <button type="button" onClick={() => void call("POST", `/v1/crm/${crm}/writes/approve`, { write_ids: [w.id] })}>Approve</button>}</td></tr>
        ))}</tbody></table>
    </section>
  );
}

export default function CrmPage() { return <SignedIn>{(s) => <Crm s={s} />}</SignedIn>; }
