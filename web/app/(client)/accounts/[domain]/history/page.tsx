"use client";
// What changed: an account's expired signals (moved to history, never deleted, RF2).
import { useParams } from "next/navigation";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";

interface Expired { fact_id: string; signal_type: string; reason: string; expired_at: string; attribute: string; grade: string | null }

function History({ domain }: { domain: string }) {
  const h = useApi<{ history: Expired[] }>(`/v1/accounts/${encodeURIComponent(domain)}/history`);
  const rows = h.data?.history ?? [];
  return (
    <section>
      <h1>History for {domain}</h1>
      <p className="muted">Signals that expired or were removed after two absent checks. Nothing is deleted.</p>
      {rows.length === 0 ? <p className="muted">Nothing has expired yet.</p> : (
        <table className="data"><thead><tr><th scope="col">Signal</th><th scope="col">Grade</th><th scope="col">Reason</th><th scope="col">When</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.fact_id}><td>{cellText(r.signal_type)}</td><td>{cellText(r.grade)}</td><td>{r.reason.replaceAll("_", " ")}</td><td>{new Date(r.expired_at).toLocaleString()}</td></tr>)}</tbody></table>
      )}
    </section>
  );
}

export default function HistoryPage() {
  const params = useParams<{ domain: string }>();
  return <SignedIn>{() => <History domain={decodeURIComponent(params.domain)} />}</SignedIn>;
}
