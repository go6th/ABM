"use client";
// The account list: every account of the tenant with its latest run; add an account.
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { SignedIn } from "@/components/SignedIn";
import { cellText } from "@/lib/render";

interface Account { domain: string; name: string | null; latest_run: { run_id: string; status: string; created_at: string } | null }

function Accounts({ canAdd }: { canAdd: boolean }) {
  const list = useApi<{ accounts: Account[] }>("/v1/accounts?limit=200");
  const accounts = list.data?.accounts ?? [];
  const [domain, setDomain] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("POST", "/v1/accounts", { domain: domain.trim().toLowerCase() });
      setDomain(""); setMessage(null); list.reload();
    } catch (err) {
      setMessage(err instanceof ApiError && err.type === "excluded_company" ? "That company is on your exclusion list." : "The account could not be added.");
    }
  };
  return (
    <section>
      <h1>Accounts</h1>
      {canAdd ? (
        <form onSubmit={add} className="row" aria-label="Add an account">
          <label htmlFor="domain">Company domain</label>
          <input id="domain" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" />
          <button className="primary" type="submit">Add account</button>
          {message ? <span className="error" role="alert">{message}</span> : null}
        </form>
      ) : null}
      <table className="data">
        <thead><tr><th scope="col">Account</th><th scope="col">Latest run</th></tr></thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.domain}>
              <td><a href={`/accounts/${encodeURIComponent(a.domain)}`}>{cellText(a.name ?? a.domain)}</a> <span className="muted">{a.domain}</span></td>
              <td>{a.latest_run ? cellText(a.latest_run.status) : <span className="muted">No run yet</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function Home() {
  return <SignedIn>{(s) => <Accounts canAdd={s.role !== "viewer"} />}</SignedIn>;
}
