"use client";

import Link from "next/link";
import { Activity, ArrowRight, ArrowUpRight, Building2, CirclePlus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { SignedIn } from "@/components/SignedIn";
import { ApiError, api } from "@/lib/api";
import { cellText } from "@/lib/render";
import { useApi } from "@/lib/useApi";
import type { AccountPage, RunPage, SignalPage } from "@/lib/gen/api.gen";
import { AdminOverview } from "@/components/AdminOverview";

const EMPTY_ACCOUNTS: AccountPage["accounts"] = [];

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

function accountNameForRun(run: { domain: string; name?: string | null }) {
  return cellText(run.name ?? run.domain);
}

export function AccountWorkspace({ canAdd }: { canAdd: boolean }) {
  const list = useApi<AccountPage>("/v1/accounts?limit=200");
  const recentRuns = useApi<RunPage>("/v1/runs?limit=8");
  const signals = useApi<SignalPage>("/v1/signals");
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
  const attentionRuns = recentRuns.data?.runs.filter((run) =>
    ["partial", "failed", "error", "blocked", "awaiting_approval", "budget_exceeded"].includes(run.status.toLowerCase()),
  ) ?? [];

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

      <div className="client-stat-grid" aria-label="Workspace summary">
        <div className="client-stat-card">
          <span className="stat-icon client-stat-icon lavender"><Building2 aria-hidden="true" /></span>
          <div><span className="stat-value">{list.error ? "—" : total ?? "…"}</span><span className="stat-label">Accounts loaded</span></div>
          <span className="stat-foot">{list.data?.next_after ? "More accounts available" : "Current account-list response"}</span>
        </div>
        <div className="client-stat-card">
          <span className="stat-icon client-stat-icon mint"><ArrowRight aria-hidden="true" /></span>
          <div><span className="stat-value">{list.error ? "—" : withRuns ?? "…"}</span><span className="stat-label">With research</span></div>
          <span className="stat-foot">Among loaded accounts</span>
        </div>
        <div className="client-stat-card">
          <span className="stat-icon client-stat-icon peach"><CirclePlus aria-hidden="true" /></span>
          <div><span className="stat-value">{list.error ? "—" : withoutRuns ?? "…"}</span><span className="stat-label">Ready to start</span></div>
          <span className="stat-foot">Among loaded accounts</span>
        </div>
      </div>

      <section className="client-accounts-panel" aria-labelledby="recent-runs-title">
        <div className="client-list-heading">
          <div><h2 id="recent-runs-title">Recent research</h2><p className="secondary">Review progress and outcomes from your latest runs.</p></div>
          {recentRuns.data?.next_cursor ? <span className="muted">Showing the latest runs</span> : null}
        </div>        {recentRuns.error ? (
          <div className="client-data-state error-box" role="alert">
            <div><strong>Recent runs could not be loaded</strong><p>{recentRuns.error.detail}</p></div>
            <button className="btn" type="button" onClick={recentRuns.reload}>Try again</button>
          </div>
        ) : !recentRuns.data ? (
          <div className="client-loading" role="status"><span className="spinner" /> Loading recent runs…</div>
        ) : recentRuns.data.runs.length === 0 ? (
          <div className="client-empty-state">
            <span className="client-empty-icon"><Activity aria-hidden="true" /></span>
            <h3>No research runs yet</h3>
            <p>Open an account to start one.</p>
          </div>
        ) : (
          <div className="client-table-wrap">
            <table className="client-account-table">
              <thead><tr><th scope="col">Status</th><th scope="col">Account</th><th scope="col">Started</th><th scope="col">Open run</th></tr></thead>
              <tbody>{recentRuns.data.runs.map((run) => {
                const dotClass = runTone(run.status).replace("tone-", "");
                return (
                  <tr key={run.run_id}>
                    <td style={{ width: "24px" }}>
                      <span className={`status-dot ${dotClass}`} title={cellText(run.label)} />
                    </td>
                    <td>
                      <Link className="client-account-name" href={`/accounts/${encodeURIComponent(run.domain)}`}>
                        <span>{cellText(accountNameForRun(run))}</span>
                        <span className="row-muted">{cellText(run.domain)}</span>
                      </Link>
                    </td>
                    <td style={{ width: "120px" }} className="row-muted">{formattedDate(run.created_at)}</td>
                    <td className="row-actions">
                      <Link className="client-row-link" href={`/runs/${encodeURIComponent(run.run_id)}`}>
                        View run <ArrowUpRight aria-hidden="true" />
                      </Link>
                    </td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </section>

      <section className="client-accounts-panel" aria-labelledby="attention-title">
        <div className="client-list-heading">
          <div><h2 id="attention-title">Recent runs needing attention</h2><p className="secondary">Review non-success outcomes returned by the run service.</p></div>
          {recentRuns.data?.next_cursor ? <Link href="/runs" className="client-row-link">View run history <ArrowUpRight aria-hidden="true" /></Link> : null}
        </div>
        {recentRuns.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>Run review status is unavailable</strong>
            <p>{recentRuns.error.detail}</p>
            <button className="btn" type="button" onClick={recentRuns.reload}>Try again</button>
          </div>
        ) : !recentRuns.data ? (
          <p className="client-loading" role="status">Checking recent run outcomes…</p>
        ) : attentionRuns.length === 0 ? (
          <div className="client-data-state"><strong>No runs need attention</strong><p>This response’s runs all look healthy.</p></div>
        ) : (
          <div className="client-table-wrap">
            <table className="client-account-table">
              <thead><tr><th scope="col">Status</th><th scope="col">Account</th><th scope="col">Started</th><th scope="col">Open run</th></tr></thead>
              <tbody>{attentionRuns.map((run) => {
                const dotClass = runTone(run.status).replace("tone-", "");
                return (
                  <tr key={run.run_id}>
                    <td style={{ width: "24px" }}>
                      <span className={`status-dot ${dotClass}`} title={cellText(run.label)} />
                    </td>
                    <td>
                      <Link className="client-account-name" href={`/accounts/${encodeURIComponent(run.domain)}`}>
                        <span>{cellText(accountNameForRun(run))}</span>
                        <span className="row-muted">{cellText(run.domain)}</span>
                      </Link>
                    </td>
                    <td style={{ width: "120px" }} className="row-muted">{formattedDate(run.created_at)}</td>
                    <td className="row-actions">
                      <Link className="client-row-link" href={`/runs/${encodeURIComponent(run.run_id)}`}>
                        Review run <ArrowUpRight aria-hidden="true" />
                      </Link>
                    </td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </section>

      <section className="client-accounts-panel" aria-labelledby="signals-title">
        <div className="client-list-heading">
          <div><h2 id="signals-title">Recent signals</h2><p className="secondary">Tenant-scoped events supplied by the signals API. Sensitive records are marked, not summarized.</p></div>
          <Link href="/alerts" className="client-row-link">Open alerts <ArrowUpRight aria-hidden="true" /></Link>
        </div>
        {signals.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>Signals could not be loaded</strong>
            <p>{signals.error.detail}</p>
            <button className="btn" type="button" onClick={signals.reload}>Try again</button>
          </div>
        ) : !signals.data ? (
          <p className="client-loading" role="status">Loading recent signals…</p>
        ) : signals.data.items.length === 0 ? (
          <p className="muted">No research signals have been returned for this workspace.</p>
        ) : (
          <div className="client-table-wrap">              <table className="client-account-table">
              <thead><tr><th scope="col">Type</th><th scope="col">Account</th><th scope="col">Kind</th><th scope="col">Detected</th></tr></thead>
              <tbody>
                {signals.data.items.slice(0, 8).map((signal) => (
                  <tr key={signal.event_id}>
                    <td style={{ width: "24px" }}>
                      <span className={`status-dot ${signal.lead ? "butter" : "sky"}`} title={signal.lead ? "Lead" : cellText(signal.grade)} />
                    </td>
                    <td><Link className="client-account-name" href={`/accounts/${encodeURIComponent(signal.account)}`}>{cellText(signal.account)}</Link></td>
                    <td className="row-muted">{signal.sensitive ? "Sensitive signal" : cellText(signal.signal_type.replaceAll("_", " "))}</td>
                    <td className="row-muted" style={{ width: "120px" }}>{formattedDate(signal.detected_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
          <div className="client-table-wrap">              <table className="client-account-table">
              <thead>
                <tr><th scope="col">Company</th><th scope="col">Latest research</th><th scope="col">Updated</th><th scope="col">Open account</th></tr>
              </thead>
              <tbody>
                {filteredAccounts.map((account) => {
                  const dotClass = account.latest_run ? runTone(account.latest_run.status).replace("tone-", "") : "slate";
                  return (
                    <tr key={account.domain}>
                      <td style={{ width: "24px" }}>
                        <span className={`status-dot ${dotClass}`} title={account.latest_run ? cellText(account.latest_run.status) : "No runs yet"} />
                      </td>
                      <td>
                        <Link className="client-account-name" href={`/accounts/${encodeURIComponent(account.domain)}`}>
                          <span>{cellText(account.name ?? account.domain)}</span>
                          <span className="row-muted">{cellText(account.domain)}</span>
                        </Link>
                      </td>
                      <td className="row-muted" style={{ width: "120px" }}>
                        {account.latest_run ? formattedDate(account.latest_run.created_at) : "—"}
                      </td>
                      <td className="row-actions">
                        <Link className="client-row-link" href={`/accounts/${encodeURIComponent(account.domain)}`}>
                          Open <ArrowUpRight aria-hidden="true" />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default function Home() {
  return (
    <SignedIn>
      {(session) => session.role === "admin"
        ? <AdminOverview />
        : <AccountWorkspace canAdd={session.role !== "viewer"} />}
    </SignedIn>
  );
}
