"use client";
// Requests for more researches or chats (owner decision 2026-10-14), on Overview and Users:
// pending first, each with the user's use and limits, a new limit to approve, and Decline. The
// user is told in the app either way; every decision is in the audit log.
import { useState } from "react";
import { operatorRequest } from "@/lib/operator-api";
import type { ConsoleLimitRequest } from "@/lib/gen/api.gen";
import { useOperatorApi } from "./useOperatorApi";
import { Pill } from "@/ui/design";
import { lastActive, num } from "@/lib/console";

function RequestRow({ r, onDone }: { r: ConsoleLimitRequest; onDone: (text: string) => void }) {
  const isResearch = r.kind === "researches";
  const current = isResearch ? r.research_limit : r.chat_limit;
  const [value, setValue] = useState(String(current + (isResearch ? 2 : 40)));
  const [busy, setBusy] = useState(false);
  const id = `req-${r.request_id}`;
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      const params = { tenant_id: r.tenant_id, request_id: r.request_id };
      if (approve) {
        const n = Math.max(0, Math.floor(Number(value) || 0));
        await operatorRequest("POST /operator/v1/console/requests/{tenant_id}/{request_id}/approve", {
          params, body: isResearch ? { research_limit: n } : { chat_limit: n },
        });
        onDone(`Approved: ${r.email} now has ${num(n)} ${r.kind}.`);
      } else {
        await operatorRequest("POST /operator/v1/console/requests/{tenant_id}/{request_id}/decline", { params });
        onDone(`Declined the request from ${r.email}.`);
      }
    } catch {
      onDone("The decision was not saved. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="lrow req-row">
      <div className="grow stack-4">
        <b>{r.email}</b>
        <span className="mut sm-text">{r.client} · asks for more {r.kind} · {lastActive(r.created_at)}</span>
        <span className="mut sm-text">Used {num(r.researches_used)} of {num(r.research_limit)} researches and {num(r.chats_used)} of {num(r.chat_limit)} chats this period</span>
        {r.note ? <span className="sm-text">&ldquo;{r.note}&rdquo;</span> : null}
      </div>
      {r.status === "pending" ? (
        <div className="row-12 wrap">
          <label className="sr-only" htmlFor={id}>New {r.kind} limit for {r.email}</label>
          <input id={id} className="input sm num-input" type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} />
          <button type="button" className="btn primary sm" disabled={busy} onClick={() => void decide(true)}>Approve</button>
          <button type="button" className="btn sm" disabled={busy} onClick={() => void decide(false)}>Decline</button>
        </div>
      ) : (
        <Pill tone={r.status === "approved" ? "ok" : "neutral"} small>{r.status === "approved" ? "Approved" : "Declined"}</Pill>
      )}
    </div>
  );
}

export function RequestsList({ pendingOnly = false }: { pendingOnly?: boolean }) {
  const data = useOperatorApi("GET /operator/v1/console/requests");
  const [message, setMessage] = useState<string | null>(null);
  const rows = (data.data?.requests ?? []).filter((r) => !pendingOnly || r.status === "pending");
  const pending = (data.data?.requests ?? []).filter((r) => r.status === "pending").length;
  return (
    <section className="card list" aria-labelledby="requests-title">
      <div className="row-space lrow">
        <h2 id="requests-title" className="card-title">Requests</h2>
        <span className="mut sm-text">{data.data ? `${num(pending)} pending` : ""}</span>
      </div>
      {message ? <p className="sm-text lrow" role="status">{message}</p> : null}
      {data.data === null && !data.error ? <div className="lrow"><div className="skel line" /></div> : null}
      {data.error ? <p className="sm-text bad-text lrow" role="alert">Requests could not be loaded. Reload the page.</p> : null}
      {rows.map((r) => <RequestRow key={r.request_id} r={r} onDone={(text) => { setMessage(text); data.reload(); }} />)}
      {data.data && !rows.length ? <p className="mut sm-text lrow">No requests for more researches or chats.</p> : null}
    </section>
  );
}
