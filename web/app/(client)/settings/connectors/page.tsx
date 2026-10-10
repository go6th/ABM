"use client";
// Client history connectors (WS6, D-65): per-source consent by an admin, folders named by the
// admin, items found outside them held off limits until an admin clears them. Content fetched
// from a connector never appears in client-facing tables or files.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { DataTable, EmptyState, type DataColumn } from "@/design/components";

interface Connection { id: string; kind: string; status: string; scopes: string[]; allowed: string[]; quarantined: number; last_fetch_at: string | null }
interface Held { id: string; resource_id: string; reason: string }

function Connectors({ s }: { s: Session }) {
  const loaded = useApi<{ connections: Connection[] }>(s.role === "admin" ? "/v1/connectors" : null);
  const list = loaded.data?.connections ?? [];
  const [held, setHeld] = useState<Record<string, Held[]>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = loaded.reload;
  if (s.role !== "admin") return <p>Only admins manage connectors.</p>;
  const report = (err: unknown) => setMsg(err instanceof ApiError ? `Request refused (${err.type}).` : "Request refused.");
  const connect = async (kind: string) => {
    setBusy(`connect:${kind}`); setMsg(null);
    try {
      const b = await api<{ authorize_url: string }>("POST", "/v1/connectors", { kind });
      window.location.assign(b.authorize_url);
    } catch (err) { report(err); setBusy(null); }
  };
  const showHeld = async (id: string) => {
    setBusy(`review:${id}`); setMsg(null);
    try { const b = await api<{ items: Held[] }>("GET", `/v1/connectors/${encodeURIComponent(id)}/quarantine`); setHeld((h) => ({ ...h, [id]: b.items })); }
    catch (err) { report(err); }
    finally { setBusy(null); }
  };
  const clear = async (id: string, item: string) => {
    setBusy(`clear:${item}`); setMsg(null);
    try {
      await api("POST", `/v1/connectors/${encodeURIComponent(id)}/quarantine/${encodeURIComponent(item)}/clear`, {});
      const b = await api<{ items: Held[] }>("GET", `/v1/connectors/${encodeURIComponent(id)}/quarantine`);
      setHeld((h) => ({ ...h, [id]: b.items }));
      setMsg("Quarantined item was cleared by the service.");
    } catch (err) { report(err); }
    finally { setBusy(null); }
  };
  const disconnect = async (connection: Connection) => {
    if (!window.confirm(`Disconnect ${connection.kind.replaceAll("_", " ")}? This stops access to that source.`)) return;
    setBusy(`disconnect:${connection.id}`); setMsg(null);
    try {
      await api("POST", `/v1/connectors/${encodeURIComponent(connection.id)}/disconnect`, {});
      setMsg("Connector disconnected.");
      load();
    } catch (err) { report(err); }
    finally { setBusy(null); }
  };
  const columns: DataColumn<Connection>[] = [
    { key: "source", header: "Source", render: (connection) => cellText(connection.kind.replaceAll("_", " ")) },
    { key: "status", header: "Status", render: (connection) => cellText(connection.status) },
    { key: "allowed", header: "Allowed sources", render: (connection) => connection.allowed.map(cellText).join(", ") || "None configured" },
    {
      key: "quarantine",
      header: "Quarantined items",
      render: (connection) => <div>
        <span>{connection.quarantined}</span>{" "}
        <button className="btn sm" type="button" disabled={busy !== null} onClick={() => void showHeld(connection.id)}>
          {busy === `review:${connection.id}` ? "Loading…" : "Review"}
        </button>
        {(held[connection.id] ?? []).map((item) => (
          <p key={item.id}>{cellText(item.resource_id)} ({cellText(item.reason)}){" "}
            <button className="btn sm" type="button" disabled={busy !== null} onClick={() => void clear(connection.id, item.id)}>Clear</button>
          </p>
        ))}
      </div>,
    },
    {
      key: "actions",
      header: "Actions",
      render: (connection) => <button className="btn sm danger" type="button" disabled={busy !== null} onClick={() => void disconnect(connection)}>
        {busy === `disconnect:${connection.id}` ? "Disconnecting…" : "Disconnect"}
      </button>,
    },
  ];
  return (
    <section className="client-dashboard">
      <header className="client-dashboard-heading"><div><p className="eyebrow">WORKSPACE ADMINISTRATION</p><h1>Integrations</h1>
        <p className="secondary">Connectors are tenant-authorized. Items outside approved sources remain quarantined.</p></div></header>
      {msg ? <p role="alert">{msg}</p> : null}
      <div className="row">
        {(["google_drive", "salesforce", "hubspot"] as const).map((kind) => (
          <button className="btn" key={kind} type="button" disabled={busy !== null} onClick={() => void connect(kind)}>
            {busy === `connect:${kind}` ? "Connecting…" : `Connect ${kind.replaceAll("_", " ")}`}
          </button>
        ))}
      </div>
      {loaded.error ? <div className="client-data-state error-box" role="alert"><strong>Connectors could not be loaded</strong><p>{loaded.error.detail}</p><button className="btn" type="button" onClick={load}>Try again</button></div>
        : !loaded.data ? <p className="client-loading" role="status">Loading integrations…</p>
          : list.length === 0 ? <EmptyState title="No connected sources" description="Connect an available source to review its approved and quarantined material." />
            : <DataTable caption="Workspace connectors" columns={columns} rows={list} rowKey={(connection) => connection.id} />}
    </section>
  );
}

export default function ConnectorsPage() { return <SignedIn>{(s) => <Connectors s={s} />}</SignedIn>; }
