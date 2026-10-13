"use client";
// Users: usage and access across clients, as counts per client. The API lists users only inside
// a client workspace, so this page shows member counts and the client enable or disable control;
// per user rows need an operator users endpoint.
import { operatorRequest } from "@/lib/operator-api";
import { useState } from "react";
import { useOperatorApi } from "../useOperatorApi";
import { Loading, Notice, PageHead, Status } from "@/ui/primitives";

export default function Users() {
  const tenants = useOperatorApi("GET /operator/v1/tenants");
  const pilot = useOperatorApi("GET /operator/v1/pilot/aggregate");
  const [busy, setBusy] = useState<string | null>(null);
  if (tenants.error) return <Notice kind="error">{tenants.error.detail}</Notice>;
  if (!tenants.data) return <Loading />;
  const questions = new Map((pilot.data?.tenants ?? []).map((t) => [t.tenant_id, t.questions_used]));
  const toggle = async (id: string, suspended: boolean) => {
    setBusy(id);
    try {
      await operatorRequest(suspended ? "POST /operator/v1/tenants/{tenant_id}/reactivate" : "POST /operator/v1/tenants/{tenant_id}/suspend", { params: { tenant_id: id }, body: { reason_code: suspended ? "operator_reenable" : "operator_disable" } });
      tenants.reload();
    } finally {
      setBusy(null);
    }
  };
  return (
    <>
      <PageHead title="Users" sub="Usage is counts only, never question text." />
      <Notice>Per user rows are not available: the API lists members only inside a client workspace. Counts per client are shown below.</Notice>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Client</th><th scope="col" className="num">Users</th><th scope="col" className="num">Questions</th><th scope="col">Access</th><th scope="col"><span className="sr-only">Action</span></th></tr></thead>
          <tbody>
            {tenants.data.tenants.map((t) => (
              <tr key={t.tenant_id}>
                <td className="mono small">{t.tenant_id}</td>
                <td className="num">{t.members}</td>
                <td className="num">{questions.get(t.tenant_id) ?? "—"}</td>
                <td>{t.suspended ? <Status tone="bad">Disabled</Status> : <Status tone="ok">Active</Status>}</td>
                <td><button type="button" className="btn sm" disabled={busy === t.tenant_id} onClick={() => void toggle(t.tenant_id, t.suspended)}>{t.suspended ? "Enable" : "Disable"}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
