"use client";
// Account Summary: the latest run and its progress, the start-run form (D-63 proposal: depth
// tiers, never credits, dollars or provider names), the five tables and the exports.
import { useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, newIdempotencyKey, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { RunProgress } from "@/components/Progress";
import { cellText } from "@/lib/render";
import { useApi } from "@/lib/useApi";
import { FeedbackForm } from "@/components/Feedback";

interface Account { domain: string; latest_run: { run_id: string; status: string } | null }
interface Settings { settings: { platform_lanes: string[]; pursuing_client: string | null } }
interface Pkg { package_id: string; kind: string; status: string; reason: string | null; files: { index: number; name: string }[] }

const TABLES: [string, string][] = [["people", "People"], ["jobs", "Job postings"], ["facts", "Facts by grade"], ["tech", "Tech stack"], ["coverage", "Coverage tracker"]];
const LANES = ["salesforce", "servicenow", "sap", "tableau_analytics", "veeva", "data_ai", "other"];

function Summary({ s, domain }: { s: Session; domain: string }) {
  const list = useApi<{ accounts: Account[] }>("/v1/accounts?limit=200");
  const st = useApi<Settings>("/v1/settings");
  const account = list.data?.accounts.find((a) => a.domain === domain) ?? null;
  const tenantLanes = st.data?.settings.platform_lanes ?? LANES;
  const client = st.data?.settings.pursuing_client ?? null;
  const [depth, setDepth] = useState<"standard" | "deep">("standard");
  const [lanesOff, setLanesOff] = useState<string[]>([]);
  const [forClient, setForClient] = useState(false);
  const [packages, setPackages] = useState<Pkg[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const canRun = s.role !== "viewer";
  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("POST", "/v1/runs", { domain, depth, lanes_off: lanesOff, pursuing_client: forClient }, { "Idempotency-Key": newIdempotencyKey() });
      setMessage("Run started."); list.reload();
    } catch (err) {
      setMessage(err instanceof ApiError ? `The run could not start (${err.type}).` : "The run could not start.");
    }
  };
  const exportAs = async (kind: "files" | "csv" | "pdf", table?: string) => {
    try {
      const pkg = await api<Pkg>("POST", "/v1/exports", table ? { domain, kind, table } : { domain, kind });
      setPackages((p) => [pkg, ...p]);
    } catch (err) {
      setMessage(err instanceof ApiError ? `Export failed (${err.type}).` : "Export failed.");
    }
  };
  return (
    <section>
      <h1>{domain}</h1>
      {account?.latest_run ? (
        <div className="card"><h2>Latest run</h2><RunProgress runId={account.latest_run.run_id} /></div>
      ) : <p className="muted">No run yet.</p>}
      {canRun ? (
        <form className="card" onSubmit={start} aria-label="Start a run">
          <h2>Start a run</h2>
          <div className="row">
            <label htmlFor="depth">Depth</label>
            <select id="depth" value={depth} onChange={(e) => setDepth(e.target.value === "deep" ? "deep" : "standard")}>
              <option value="standard">Standard</option><option value="deep">Deep</option>
            </select>
          </div>
          <fieldset><legend>Platform lanes for this run</legend>
            {tenantLanes.map((lane) => (
              <label key={lane} className="row">
                <input type="checkbox" checked={!lanesOff.includes(lane)} onChange={(e) => setLanesOff((o) => e.target.checked ? o.filter((x) => x !== lane) : [...o, lane])} />
                {lane.replaceAll("_", " ")}
              </label>
            ))}
          </fieldset>
          {client ? (
            <label className="row"><input type="checkbox" checked={forClient} onChange={(e) => setForClient(e.target.checked)} /> Include history with {cellText(client)}</label>
          ) : null}
          <button className="primary" type="submit">Start run</button>
        </form>
      ) : null}
      {message ? <p role="status">{message}</p> : null}
      {account?.latest_run ? <FeedbackForm label="Feedback on this account" target={{ domain }} /> : null}
      <h2>Tables</h2>
      <ul>{TABLES.map(([key, label]) => <li key={key}><a href={`/accounts/${encodeURIComponent(domain)}/tables/${key}`}>{label}</a></li>)}</ul>
      <h2>Exports</h2>
      <div className="row">
        <button type="button" onClick={() => void exportAs("files")}>Word and Excel files</button>
        <button type="button" onClick={() => void exportAs("pdf")}>Account Summary PDF</button>
        {TABLES.map(([key, label]) => <button key={key} type="button" onClick={() => void exportAs("csv", key)}>{label} CSV</button>)}
      </div>
      <ul>
        {packages.map((p) => (
          <li key={p.package_id}>
            {p.kind}: {p.status === "delivered" ? p.files.map((f) => (
              <a key={f.index} href={`/v1/exports/${p.package_id}/files/${f.index}`}>{cellText(f.name)} </a>
            )) : <span className="error">not delivered ({cellText(p.reason)})</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function AccountPage() {
  const params = useParams<{ domain: string }>();
  const domain = decodeURIComponent(params.domain);
  return <SignedIn>{(s) => <Summary s={s} domain={domain} />}</SignedIn>;
}
