"use client";
// Client history connectors (WS6, D-65): per-source consent by an admin, folders named by the
// admin, items found outside them held off limits until an admin clears them. Content fetched
// from a connector never appears in client-facing tables or files.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";

interface Connection { id: string; kind: string; status: string; scopes: string[]; allowed: string[]; quarantined: number; last_fetch_at: string | null }
interface Held { id: string; resource_id: string; reason: string }

function Connectors({ s }: { s: Session }) {
  const loaded = useApi<{ connections: Connection[] }>(s.role === "admin" ? "/v1/connectors" : null);
  const list = loaded.data?.connections ?? [];
  const [held, setHeld] = useState<Record<string, Held[]>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const load = loaded.reload;
  if (s.role !== "admin") return <p>Only admins manage connectors.</p>;
  const report = (err: unknown) => setMsg(err instanceof ApiError ? `Refused (${err.type}).` : "Refused.");
  const connect = async (kind: string) => {
    try { const b = await api<{ authorize_url: string }>("POST", "/v1/connectors", { kind }); window.location.assign(b.authorize_url); } catch (err) { report(err); }
  };
  const showHeld = async (id: string) => { try { const b = await api<{ items: Held[] }>("GET", `/v1/connectors/${id}/quarantine`); setHeld((h) => ({ ...h, [id]: b.items })); } catch (err) { report(err); } };
  const clear = async (id: string, item: string) => { try { await api("POST", `/v1/connectors/${id}/quarantine/${item}/clear`, {}); await showHeld(id); } catch (err) { report(err); } };
  const disconnect = async (id: string) => { try { await api("POST", `/v1/connectors/${id}/disconnect`, {}); load(); } catch (err) { report(err); } };
  return (
    <section>
      <h1>Connectors</h1>
      {msg ? <p role="alert">{msg}</p> : null}
      <div className="row">
        <button type="button" onClick={() => void connect("google_drive")}>Connect Google Drive</button>
        <button type="button" onClick={() => void connect("salesforce")}>Connect Salesforce</button>
        <button type="button" onClick={() => void connect("hubspot")}>Connect HubSpot</button>
      </div>
      <table className="data"><thead><tr><th scope="col">Source</th><th scope="col">Status</th><th scope="col">Allowed</th><th scope="col">Off limits</th><th scope="col">Actions</th></tr></thead>
        <tbody>{list.map((c) => (
          <tr key={c.id}><td>{c.kind.replaceAll("_", " ")}</td><td>{c.status}</td><td>{c.allowed.map(cellText).join(", ")}</td>
            <td>{c.quarantined} <button type="button" onClick={() => void showHeld(c.id)}>Review</button>
              {(held[c.id] ?? []).map((h) => <div key={h.id}>{cellText(h.resource_id)} ({h.reason}) <button type="button" onClick={() => void clear(c.id, h.id)}>Clear</button></div>)}</td>
            <td><button type="button" onClick={() => void disconnect(c.id)}>Disconnect</button></td></tr>
        ))}</tbody></table>
    </section>
  );
}

export default function ConnectorsPage() { return <SignedIn>{(s) => <Connectors s={s} />}</SignedIn>; }
