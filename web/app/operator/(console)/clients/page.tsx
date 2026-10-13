"use client";
// Clients: a table of every client workspace (by id, never by content). Click a client to see
// its counts and an enable or disable control. No caps of any kind anywhere.
import { useState } from "react";
import { operatorRequest } from "@/lib/operator-api";
import { useOperatorApi } from "../useOperatorApi";
import { Loading, Notice, PageHead, Status } from "@/ui/primitives";
import { day } from "@/lib/ws-format";

export default function Clients() {
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (tenants.error) return <Notice kind="error">{tenants.error.detail}</Notice>;
  if (!tenants.data) return <Loading />;
  const rows = tenants.data.tenants;
  const chosen = rows.find((t) => t.tenant_id === open) ?? null;
  const toggle = async (id: string, suspended: boolean) => {
    setBusy(true);
    setMessage(null);
    try {
      await operatorRequest(suspended ? "POST /operator/v1/tenants/{tenant_id}/reactivate" : "POST /operator/v1/tenants/{tenant_id}/suspend", { params: { tenant_id: id }, body: { reason_code: suspended ? "operator_reenable" : "operator_disable" } });
      setMessage(suspended ? "Client enabled." : "Client disabled.");
      tenants.reload();
    } catch {
      setMessage("The change was not saved. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title="Clients" sub={`${rows.length} clients. Each client is a sealed workspace.`} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Client</th><th scope="col" className="num">Users</th><th scope="col" className="num">Accounts</th><th scope="col" className="num">Runs now</th><th scope="col">Created</th><th scope="col">Status</th></tr></thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.tenant_id} className="row-link" aria-selected={open === t.tenant_id || undefined} onClick={() => setOpen(t.tenant_id)}>
                <td><button type="button" className="link-btn mono small" onClick={(e) => { e.stopPropagation(); setOpen(t.tenant_id); }}>{t.tenant_id}</button></td>
                <td className="num">{t.members}</td>
                <td className="num">{t.accounts}</td>
                <td className="num">{t.live_runs}</td>
                <td>{day(t.created_at)}</td>
                <td>{t.suspended ? <Status tone="bad">Disabled</Status> : <Status tone="ok">Active</Status>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {chosen ? (
        <section className="section" aria-label="Client">
          <h2 className="mono">{chosen.tenant_id}</h2>
          <dl className="dl">
            <div className="contents"><dt>Users</dt><dd>{chosen.members}</dd></div>
            <div className="contents"><dt>Accounts</dt><dd>{chosen.accounts}</dd></div>
            <div className="contents"><dt>Runs now</dt><dd>{chosen.live_runs}</dd></div>
            <div className="contents"><dt>Idle access expiry</dt><dd>Off (not configurable by the API yet)</dd></div>
          </dl>
          <p className="small muted">Members are listed by the client&apos;s own workspace. This console sees counts only.</p>
          {message ? <p className="small" role="status">{message}</p> : null}
          <div className="actions">
            <button type="button" className={chosen.suspended ? "btn primary" : "btn danger"} disabled={busy} onClick={() => void toggle(chosen.tenant_id, chosen.suspended)}>{chosen.suspended ? "Enable client" : "Disable client"}</button>
            <button type="button" className="btn" onClick={() => setOpen(null)}>Close</button>
          </div>
        </section>
      ) : null}
    </>
  );
}
