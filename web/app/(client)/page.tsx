"use client";

import Link from "next/link";
import { ArrowRight, ArrowUpRight, Building2, CirclePlus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { SignedIn } from "@/components/SignedIn";
import { ApiError, api } from "@/lib/api";
import { cellText } from "@/lib/render";
import { useApi } from "@/lib/useApi";

interface Account {
  domain: string;
  name: string | null;
  latest_run: { run_id: string; status: string; created_at: string } | null;
}

const EMPTY_ACCOUNTS: Account[] = [];

function formattedDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function runTone(status: string): string {
  const value = status.toLowerCase();
  if (["complete", "completed", "succeeded", "success"].includes(value)) return "tone-mint";
  if (["partial", "awaiting_approval"].includes(value)) return "tone-butter";
  if (["failed", "error", "cancelled", "canceled", "budget_exceeded"].includes(value)) return "tone-rose";
  if (["queued", "running", "distilling", "verifying", "in_progress", "waiting"].includes(value)) return "tone-sky";
  return "tone-slate";
}

function AccountWorkspace({ canAdd }: { canAdd: boolean }) {
  const list = useApi<{ accounts: Account[] }>("/v1/accounts?limit=200");
  const accounts = list.data?.accounts ?? EMPTY_ACCOUNTS;
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const filteredAccounts = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return accounts;
    return accounts.filter((account) =>
      `${account.name ?? ""} ${account.domain}`.toLowerCase().includes(needle),
    );
  }, [accounts, query]);

  const add = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedDomain = domain.trim().toLowerCase();
    if (!normalizedDomain) {
      setMessage({ kind: "error", text: "Enter a company domain to continue." });
      return;
    }
    try {
      await api("POST", "/v1/accounts", { domain: normalizedDomain });
      setDomain("");
      setMessage({ kind: "success", text: "Account added to your workspace." });
      list.reload();
    } catch (error) {
      const text = error instanceof ApiError && error.type === "excluded_company"
        ? "This company is on your workspace exclusion list."
        : "The account could not be added. Check the domain and try again.";
      setMessage({ kind: "error", text });
    }
  };

  const total = list.data ? accounts.length : null;
  const withRuns = list.data ? accounts.filter((account) => account.latest_run !== null).length : null;
  const withoutRuns = total === null || withRuns === null ? null : total - withRuns;

  return (
    <div className="client-dashboard">
      <section className="client-dashboard-heading" aria-labelledby="workspace-title">
        <div>
          <p className="eyebrow">ACCOUNT INTELLIGENCE</p>
          <h1 id="workspace-title">Your workspace</h1>
          <p className="secondary">Keep company research organized and ready for your team.</p>
        </div>
        {canAdd ? <a className="btn primary" href="#add-account"><CirclePlus aria-hidden="true" /> Add account</a> : null}
      </section>

      <section className="client-stat-grid" aria-label="Workspace summary">
        <article className="client-stat-card">
          <span className="client-stat-icon lavender"><Building2 aria-hidden="true" /></span>
          <div><span className="client-stat-label">Accounts</span><strong>{list.error ? "—" : total ?? "…"}</strong></div>
          <span className="client-stat-foot">In this workspace</span>
        </article>
        <article className="client-stat-card">
          <span className="client-stat-icon mint"><ArrowRight aria-hidden="true" /></span>
          <div><span className="client-stat-label">With research</span><strong>{list.error ? "—" : withRuns ?? "…"}</strong></div>
          <span className="client-stat-foot">Accounts with a latest run</span>
        </article>
        <article className="client-stat-card">
          <span className="client-stat-icon peach"><CirclePlus aria-hidden="true" /></span>
          <div><span className="client-stat-label">Ready to start</span><strong>{list.error ? "—" : withoutRuns ?? "…"}</strong></div>
          <span className="client-stat-foot">No run recorded yet</span>
        </article>
      </section>

      {canAdd ? (
        <section className="client-add-panel" id="add-account" aria-labelledby="add-account-title">
          <div className="client-add-copy">
            <span className="client-panel-icon"><CirclePlus aria-hidden="true" /></span>
            <div><h2 id="add-account-title">Add a company</h2><p>Enter a company domain to add it to your research workspace.</p></div>
          </div>
          <form className="client-add-form" onSubmit={add}>
            <label className="sr-only" htmlFor="account-domain">Company domain</label>
            <input
              id="account-domain"
              className="input"
              name="domain"
              autoComplete="url"
              placeholder="example.com"
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              aria-describedby={message ? "add-account-message" : undefined}
            />
            <button className="btn primary" type="submit">Add account <ArrowRight aria-hidden="true" /></button>
            {message ? <p className={`client-form-message ${message.kind}`} id="add-account-message" role={message.kind === "error" ? "alert" : "status"}>{message.text}</p> : null}
          </form>
        </section>
      ) : null}

      <section className="client-accounts-panel" aria-labelledby="accounts-title">
        <div className="client-list-heading">
          <div><h2 id="accounts-title">Accounts</h2><p className="secondary">Company profiles and the latest research run.</p></div>
          {list.data && accounts.length > 0 ? (
            <label className="client-search">
              <Search aria-hidden="true" />
              <span className="sr-only">Search accounts</span>
              <input className="input" type="search" placeholder="Search accounts" value={query} onChange={(event) => setQuery(event.target.value)} />
            </label>
          ) : null}
        </div>

        {list.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>{list.error.status === 503 ? "Workspace service unavailable" : "Accounts could not be loaded"}</strong>
            <p>{list.error.detail}</p>
            <button className="btn" type="button" onClick={list.reload}>Try again</button>
          </div>
        ) : !list.data ? (
          <div className="client-loading" role="status" aria-live="polite"><span className="spinner" /> Loading your accounts…</div>
        ) : accounts.length === 0 ? (
          <div className="client-empty-state">
            <span className="client-empty-icon"><Building2 aria-hidden="true" /></span>
            <h3>Your workspace is ready</h3>
            <p>Add a company to start organizing account research.</p>
            {canAdd ? <a className="btn primary" href="#add-account"><CirclePlus aria-hidden="true" /> Add your first account</a> : null}
          </div>
        ) : filteredAccounts.length === 0 ? (
          <div className="client-data-state"><strong>No matching accounts</strong><p>Try a different company name or domain.</p></div>
        ) : (
          <div className="client-table-wrap">
            <table className="client-account-table">
              <thead><tr><th scope="col">Company</th><th scope="col">Latest research</th><th scope="col">Last updated</th><th scope="col"><span className="sr-only">Open account</span></th></tr></thead>
              <tbody>
                {filteredAccounts.map((account) => (
                  <tr key={account.domain}>
                    <td><Link className="client-account-name" href={`/accounts/${encodeURIComponent(account.domain)}`}>{cellText(account.name ?? account.domain)}<span>{cellText(account.domain)}</span></Link></td>
                    <td>{account.latest_run ? <span className={`badge ${runTone(account.latest_run.status)}`}>{cellText(account.latest_run.status)}</span> : <span className="muted">No runs yet</span>}</td>
                    <td>{formattedDate(account.latest_run?.created_at ?? null)}</td>
                    <td><Link className="client-row-link" href={`/accounts/${encodeURIComponent(account.domain)}`} aria-label={`Open ${cellText(account.name ?? account.domain)}`}>Open <ArrowUpRight aria-hidden="true" /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default function Home() {
  return <SignedIn>{(session) => <AccountWorkspace canAdd={session.role !== "viewer"} />}</SignedIn>;
}
