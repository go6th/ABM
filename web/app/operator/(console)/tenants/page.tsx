"use client";
// Tenants: content-free inventory with audited lifecycle, plan and quota controls, provisioning,
// and deletion on request. Deletion takes two people: one opens the request and runs the dry run,
// a second operator approves, then execute runs bounded, resumable passes. The request id is
// shown so the second operator can continue it.
import { useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Plus, Trash2, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import { formatCount, formatDate } from "@/lib/operator-render";
import { humanize } from "@/lib/ws-format";
import { useOperatorViewer } from "../OperatorSessionGate";
import { OperatorRequestState } from "../OperatorStates";
import { useOperatorApi } from "../useOperatorApi";
import { Badge, Banner, PageHead, Section } from "@/ui/primitives";

const REASON = "[a-z_]{1,40}";

function TenantActions({ tenantId, suspended, onChanged, onDelete }: { tenantId: string; suspended: boolean; onChanged: () => void; onDelete: () => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function mutate(event: FormEvent<HTMLFormElement>, kind: "lifecycle" | "quota" | "plan") {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setMessage(null);
    try {
      if (kind === "lifecycle") {
        const reason_code = String(form.get("reason_code") ?? "");
        await operatorRequest(suspended ? "POST /operator/v1/tenants/{tenant_id}/reactivate" : "POST /operator/v1/tenants/{tenant_id}/suspend", {
          params: { tenant_id: tenantId }, body: { reason_code },
        });
      } else if (kind === "quota") {
        await operatorRequest("POST /operator/v1/tenants/{tenant_id}/quotas", {
          params: { tenant_id: tenantId },
          body: { dimension: form.get("dimension"), limit_value: Number(form.get("limit_value")), reason_code: form.get("reason_code") },
        });
      } else {
        await operatorRequest("POST /operator/v1/tenants/{tenant_id}/plan", {
          params: { tenant_id: tenantId }, body: { plan_code: form.get("plan_code"), reason_code: form.get("reason_code") },
        });
      }
      setMessage({ ok: true, text: "Saved. The operator audit trail was updated." });
      onChanged();
    } catch (error) {
      setMessage({ ok: false, text: error instanceof ApiError ? error.detail : "The request could not be completed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack tenant-actions">
      <details className="disclosure">
        <summary>Lifecycle</summary>
        <form className="form-row" onSubmit={(event) => void mutate(event, "lifecycle")}>
          <div className="field"><label htmlFor={`lc-${tenantId}`}>Reason code</label><input id={`lc-${tenantId}`} className="input mono" name="reason_code" pattern={REASON} maxLength={40} placeholder="policy_review" required /></div>
          <button className={`btn sm ${suspended ? "primary" : "danger"}`} disabled={busy} type="submit">{suspended ? "Reactivate tenant" : "Suspend tenant"}</button>
        </form>
      </details>
      <details className="disclosure">
        <summary>Set plan</summary>
        <form className="form-row" onSubmit={(event) => void mutate(event, "plan")}>
          <div className="field"><label htmlFor={`pc-${tenantId}`}>Plan code</label><input id={`pc-${tenantId}`} className="input mono" name="plan_code" pattern={REASON} maxLength={40} placeholder="standard" required /></div>
          <div className="field"><label htmlFor={`pr-${tenantId}`}>Reason code</label><input id={`pr-${tenantId}`} className="input mono" name="reason_code" pattern={REASON} maxLength={40} placeholder="contract_change" required /></div>
          <button className="btn sm" disabled={busy} type="submit">Assign plan</button>
        </form>
      </details>
      <details className="disclosure">
        <summary>Quota override</summary>
        <form className="form-row" onSubmit={(event) => void mutate(event, "quota")}>
          <div className="field"><label htmlFor={`qd-${tenantId}`}>Dimension</label><select id={`qd-${tenantId}`} className="select" name="dimension" defaultValue="accounts"><option value="accounts">Accounts</option><option value="runs_per_period">Runs per period</option><option value="refreshes_per_period">Refreshes per period</option><option value="seats">Seats</option><option value="connectors">Connectors</option><option value="storage_mb">Storage in MB</option></select></div>
          <div className="field"><label htmlFor={`ql-${tenantId}`}>Limit</label><input id={`ql-${tenantId}`} className="input num" name="limit_value" type="number" min={0} max={1000000} step={1} required /></div>
          <div className="field"><label htmlFor={`qr-${tenantId}`}>Reason code</label><input id={`qr-${tenantId}`} className="input mono" name="reason_code" pattern={REASON} maxLength={40} placeholder="approved_change" required /></div>
          <button className="btn sm" disabled={busy} type="submit">Set quota</button>
        </form>
      </details>
      <div><button type="button" className="btn sm quiet" onClick={onDelete}><Trash2 size={14} aria-hidden="true" />Deletion</button></div>
      {message ? <p className={message.ok ? "inline-status" : "inline-error"} role="status">{message.text}</p> : null}
    </div>
  );
}

type Step = { kind: "request" } | { kind: "dry-run" | "approve" | "execute"; requestId: string };

function DeletionFlow({ tenantId, onClose }: { tenantId: string; onClose: () => void }) {
  const [requestId, setRequestId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<Array<{ ok: boolean; text: string }>>([]);
  const [confirmText, setConfirmText] = useState("");
  const note = (ok: boolean, text: string) => setLog((all) => [...all, { ok, text }]);
  const run = async (step: Step) => {
    setBusy(true);
    try {
      if (step.kind === "request") {
        const r = await operatorRequest("POST /operator/v1/tenants/{tenant_id}/deletion", { params: { tenant_id: tenantId }, body: { reason_code: reason } });
        setRequestId(r.request_id);
        note(true, `Request opened: ${r.request_id}. Run the dry run next.`);
      } else {
        const key = step.kind === "dry-run" ? "POST /operator/v1/tenants/{tenant_id}/deletion/{request_id}/dry-run"
          : step.kind === "approve" ? "POST /operator/v1/tenants/{tenant_id}/deletion/{request_id}/approve"
            : "POST /operator/v1/tenants/{tenant_id}/deletion/{request_id}/execute";
        const r = await operatorRequest(key, { params: { tenant_id: tenantId, request_id: step.requestId } });
        if (step.kind === "dry-run") {
          const counts = Object.entries(r.erase ?? {}).map(([k, v]) => `${humanize(k)} ${v ?? "unknown"}`).join(", ");
          note(true, `Dry run: ${counts || "nothing to erase"}.${r.blocked ? " Blocked items are listed in the audit trail." : ""}`);
        } else if (step.kind === "approve") {
          note(true, `Approved${r.approved_by ? ` by ${r.approved_by}` : ""}. Execute when ready.`);
        } else {
          note(true, `Pass finished${r.exit_reason ? `: ${humanize(r.exit_reason)}` : ""}. Run again until nothing is left.`);
        }
      }
    } catch (error) {
      note(false, error instanceof ApiError ? `${error.detail} (${error.type})` : "The step could not be completed.");
    } finally {
      setBusy(false);
    }
  };
  const id = requestId.trim();
  const validId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id);
  return (
    <div className="stack">
      <Banner kind="warn" title="Deletion takes two operators">One opens the request and runs the dry run. A second operator approves. Execute erases the tenant in bounded passes.</Banner>
      <ol className="steps" aria-label="Deletion steps">
        <li data-done={validId || undefined} aria-current={!validId ? "step" : undefined}>Request</li>
        <li aria-current={validId ? "step" : undefined}>Dry run</li>
        <li>Approve</li>
        <li>Execute</li>
      </ol>
      <form className="form-row" onSubmit={(e) => { e.preventDefault(); void run({ kind: "request" }); }}>
        <div className="field"><label htmlFor="del-reason">Reason code</label><input id="del-reason" className="input mono" pattern={REASON} maxLength={40} required placeholder="client_request" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
        <button className="btn danger" type="submit" disabled={busy || !reason}>Open deletion request</button>
      </form>
      <div className="field">
        <label htmlFor="del-id">Request ID</label>
        <input id="del-id" className="input mono" value={requestId} onChange={(e) => setRequestId(e.target.value)} placeholder="Paste a request ID to continue it" aria-describedby="del-id-hint" />
        <p className="hint" id="del-id-hint">The second operator pastes the request ID here to approve it.</p>
      </div>
      <div className="row">
        <button className="btn" type="button" disabled={busy || !validId} onClick={() => void run({ kind: "dry-run", requestId: id })}>Run dry run</button>
        <button className="btn tinted" type="button" disabled={busy || !validId} onClick={() => void run({ kind: "approve", requestId: id })}>Approve as second operator</button>
      </div>
      <div className="field">
        <label htmlFor="del-confirm">Type the tenant ID to execute</label>
        <input id="del-confirm" className="input mono" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoComplete="off" />
      </div>
      <div className="row between">
        <button className="btn danger" type="button" disabled={busy || !validId || confirmText.trim() !== tenantId} onClick={() => void run({ kind: "execute", requestId: id })}>Execute a deletion pass</button>
        <button className="btn quiet" type="button" onClick={onClose}>Close</button>
      </div>
      {log.length ? (
        <ul className="stack" aria-live="polite" aria-label="Deletion log">
          {log.map((entry, i) => <li key={i} className={entry.ok ? "inline-status" : "inline-error"}>{entry.text}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

export default function OperatorTenantsPage() {
  const viewer = useOperatorViewer();
  const isOperator = viewer.role === "operator";
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const [createMessage, setCreateMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  async function createTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!name) return;
    setCreating(true);
    setCreateMessage(null);
    try {
      const created = await operatorRequest("POST /operator/v1/tenants", { body: { name } });
      setCreateMessage({ ok: true, text: `Tenant ${created.tenant_id} was provisioned with an empty workspace.` });
      form.reset();
      tenants.reload();
    } catch (error) {
      setCreateMessage({ ok: false, text: error instanceof ApiError ? error.detail : "The tenant could not be provisioned." });
    } finally {
      setCreating(false);
    }
  }

  return (
    <div>
      <PageHead title="Tenants" sub="Content-free tenant inventory with audited lifecycle, plan and quota controls." />
      {isOperator ? (
        <form className="card form-row" onSubmit={(event) => void createTenant(event)} aria-label="Provision a workspace">
          <div className="field"><label htmlFor="tenant-name">Workspace name</label><input id="tenant-name" className="input" name="name" minLength={1} maxLength={120} required /></div>
          <button className="btn primary" disabled={creating} type="submit"><Plus size={16} aria-hidden="true" />{creating ? "Creating" : "Create tenant"}</button>
        </form>
      ) : <Banner kind="info" title="Read-only support role" role="status">Provisioning and lifecycle, plan and quota changes require the operator role.</Banner>}
      {createMessage ? <p className={createMessage.ok ? "inline-status block-gap" : "inline-error block-gap"} role="status">{createMessage.text}</p> : null}

      <Section title="Tenant inventory" id="inventory" sub="Live from the operator API. No account content.">
        <OperatorRequestState loading={tenants.loading} error={tenants.error} empty={Boolean(tenants.data && tenants.data.tenants.length === 0)} onRetry={tenants.reload} />
        {tenants.data && tenants.data.tenants.length > 0 ? (
          <div className="table-wrap"><table className="table">
            <caption className="sr-only">Tenant IDs, plan, lifecycle state and backend-provided counts</caption>
            <thead><tr><th scope="col">Tenant ID</th><th scope="col">Created</th><th scope="col">Plan</th><th scope="col">State</th><th scope="col" className="num">Members</th><th scope="col" className="num">Accounts</th><th scope="col" className="num">Live runs</th>{isOperator ? <th scope="col">Admin actions</th> : null}</tr></thead>
            <tbody>{tenants.data.tenants.map((tenant) => (
              <tr key={tenant.tenant_id}>
                <td><code className="mono t-footnote">{tenant.tenant_id}</code></td><td className="nowrap">{formatDate(tenant.created_at)}</td><td>{tenant.plan ?? "Unassigned"}</td>
                <td><Badge tone={tenant.suspended ? "rose" : "mint"} dot>{tenant.suspended ? "Suspended" : "Active"}</Badge></td>
                <td className="num">{formatCount(tenant.members)}</td><td className="num">{formatCount(tenant.accounts)}</td><td className="num">{formatCount(tenant.live_runs)}</td>
                {isOperator ? <td><TenantActions tenantId={tenant.tenant_id} suspended={tenant.suspended} onChanged={tenants.reload} onDelete={() => setDeleting(tenant.tenant_id)} /></td> : null}
              </tr>
            ))}</tbody>
          </table></div>
        ) : null}
      </Section>

      <Dialog.Root open={deleting !== null} onOpenChange={(v) => { if (!v) setDeleting(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="sheet" aria-describedby={undefined}>
            <header>
              <Dialog.Title>Delete tenant</Dialog.Title>
              <Dialog.Close className="icon-btn" aria-label="Close"><X size={18} strokeWidth={1.7} /></Dialog.Close>
            </header>
            <div className="body">
              {deleting ? <><p className="mono t-footnote ink-2">{deleting}</p><DeletionFlow key={deleting} tenantId={deleting} onClose={() => setDeleting(null)} /></> : null}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
