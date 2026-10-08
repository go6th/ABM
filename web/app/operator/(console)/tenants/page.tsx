"use client";

import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import { formatCount, formatDate } from "@/lib/operator-render";
import { useOperatorViewer } from "../OperatorSessionGate";
import { OperatorRequestState } from "../OperatorStates";
import { useOperatorApi } from "../useOperatorApi";

function TenantActions({ tenantId, suspended, onChanged }: { tenantId: string; suspended: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function mutate(event: FormEvent<HTMLFormElement>, kind: "lifecycle" | "quota" | "plan") {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setMessage("");
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
      setMessage("Saved. The operator audit trail was updated.");
      onChanged();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.detail : "The request could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tenant-admin-actions">
      <details>
        <summary>Lifecycle</summary>
        <form className="operator-inline-form" onSubmit={(event) => void mutate(event, "lifecycle")}>
          <label className="field"><span>Reason code</span><input name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="policy_review" required /></label>
          <button className={`btn sm ${suspended ? "primary" : "danger"}`} disabled={busy} type="submit">{suspended ? "Reactivate tenant" : "Suspend tenant"}</button>
        </form>
      </details>
      <details>
        <summary>Set plan</summary>
        <form className="operator-inline-form" onSubmit={(event) => void mutate(event, "plan")}>
          <label className="field"><span>Plan code</span><input name="plan_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="standard" required /></label>
          <label className="field"><span>Reason code</span><input name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="contract_change" required /></label>
          <button className="btn sm" disabled={busy} type="submit">Assign plan</button>
        </form>
      </details>
      <details>
        <summary>Quota override</summary>
        <form className="operator-inline-form" onSubmit={(event) => void mutate(event, "quota")}>
          <label className="field"><span>Dimension</span><select name="dimension" defaultValue="accounts"><option value="accounts">Accounts</option><option value="runs_per_period">Runs / period</option><option value="refreshes_per_period">Refreshes / period</option><option value="seats">Seats</option><option value="connectors">Connectors</option><option value="storage_mb">Storage (MB)</option></select></label>
          <label className="field"><span>Limit</span><input name="limit_value" type="number" min={0} max={1000000} step={1} required /></label>
          <label className="field"><span>Reason code</span><input name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="approved_change" required /></label>
          <button className="btn sm" disabled={busy} type="submit">Set quota</button>
        </form>
      </details>
      {message ? <p className="operator-action-message" role="status">{message}</p> : null}
    </div>
  );
}

export default function OperatorTenantsPage() {
  const viewer = useOperatorViewer();
  const isOperator = viewer.role === "operator";
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const [createMessage, setCreateMessage] = useState("");
  const [creating, setCreating] = useState(false);

  async function createTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!name) return;
    setCreating(true);
    setCreateMessage("");
    try {
      const created = await operatorRequest("POST /operator/v1/tenants", { body: { name } });
      setCreateMessage(`Tenant ${created.tenant_id} was provisioned with an empty workspace.`);
      form.reset();
      tenants.reload();
    } catch (error) {
      setCreateMessage(error instanceof ApiError ? error.detail : "The tenant could not be provisioned.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <section>
      <div className="operator-page-heading"><div><p className="eyebrow">PLATFORM OPERATIONS</p><h1>Tenants</h1><p>Content-free tenant inventory with audited lifecycle and quota controls.</p></div></div>
      {isOperator ? (
        <form className="operator-create-form" onSubmit={(event) => void createTenant(event)}>
          <div><strong>Provision a workspace</strong><p>Creates an empty tenant and records the staff action.</p></div>
          <label className="field"><span>Workspace name</span><input name="name" minLength={1} maxLength={120} required /></label>
          <button className="btn primary" disabled={creating} type="submit">{creating ? "Creating…" : "Create tenant"}</button>
          {createMessage ? <p className="operator-action-message" role="status">{createMessage}</p> : null}
        </form>
      ) : <div className="banner tone-sky" role="status"><span aria-hidden="true">ⓘ</span><div className="banner-body"><strong>Read-only support role</strong><p>Provisioning and lifecycle, plan, and quota changes require the operator role.</p></div></div>}

      <div className="operator-section-title operator-section"><h2>Tenant inventory</h2><span className="muted">Live response · no account content</span></div>
      <OperatorRequestState loading={tenants.loading} error={tenants.error} empty={Boolean(tenants.data && tenants.data.tenants.length === 0)} onRetry={tenants.reload} />
      {tenants.data && tenants.data.tenants.length > 0 ? (
        <div className="operator-table-scroll"><table className="operator-table">
          <caption className="sr-only">Tenant IDs, plan, lifecycle state and backend-provided counts</caption>
          <thead><tr><th scope="col">Tenant ID</th><th scope="col">Created</th><th scope="col">Plan</th><th scope="col">State</th><th scope="col">Members</th><th scope="col">Accounts</th><th scope="col">Live runs</th>{isOperator ? <th scope="col">Admin actions</th> : null}</tr></thead>
          <tbody>{tenants.data.tenants.map((tenant) => (
            <tr key={tenant.tenant_id}>
              <td><code>{tenant.tenant_id}</code></td><td>{formatDate(tenant.created_at)}</td><td>{tenant.plan ?? "Unassigned"}</td>
              <td><span className={`badge ${tenant.suspended ? "tone-rose" : "tone-mint"}`}>{tenant.suspended ? "Suspended" : "Active"}</span></td>
              <td>{formatCount(tenant.members)}</td><td>{formatCount(tenant.accounts)}</td><td>{formatCount(tenant.live_runs)}</td>
              {isOperator ? <td><TenantActions tenantId={tenant.tenant_id} suspended={tenant.suspended} onChanged={tenants.reload} /></td> : null}
            </tr>
          ))}</tbody>
        </table></div>
      ) : null}
    </section>
  );
}
