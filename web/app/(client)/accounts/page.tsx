"use client";

import { ArrowUpRight, CirclePlus } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { DataTable, EmptyState, Input, type DataColumn } from "@/design/components";
import { cellText } from "@/lib/render";
import { useApi } from "@/lib/useApi";
import { SignedIn } from "@/components/SignedIn";
import type { AccountItem, AccountPage } from "@/lib/gen/api.gen";

function formattedDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function AccountList({ canAdd }: { canAdd: boolean }) {
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | null>>([]);
  const requestPath = useMemo(() => {
    const query = new URLSearchParams({ limit: "50" });
    if (cursor) query.set("after", cursor);
    if (search.trim()) query.set("search", search.trim());
    return `/v1/accounts?${query.toString()}`;
  }, [cursor, search]);
  const page = useApi<AccountPage>(requestPath);

  const columns: DataColumn<AccountItem>[] = [
    {
      key: "company",
      header: "Account",
      render: (account) => (
        <Link className="client-account-name" href={`/accounts/${encodeURIComponent(account.domain)}`}>
          <strong>{cellText(account.name ?? account.domain)}</strong>
          <span>{cellText(account.domain)}</span>
        </Link>
      ),
    },
    { key: "status", header: "Latest research", render: (account) => account.latest_run?.status ?? "No runs yet" },
    { key: "updated", header: "Last attempted", render: (account) => formattedDate(account.latest_run?.created_at ?? null) },
    {
      key: "open",
      header: "Open",
      render: (account) => (
        <Link className="client-row-link" href={`/accounts/${encodeURIComponent(account.domain)}`}>
          View account <ArrowUpRight aria-hidden="true" />
        </Link>
      ),
    },
  ];

  const changeSearch = (value: string) => {
    setSearch(value);
    setCursor(null);
    setHistory([]);
  };

  return (
    <div className="client-dashboard">
      <section className="client-dashboard-heading">
        <div>
          <p className="eyebrow">WORKSPACE</p>
          <h1>Accounts</h1>
          <p className="secondary">Manage target accounts and inspect their latest research activity.</p>
        </div>
        {canAdd ? (
          <Link className="btn primary" href="/#add-account"><CirclePlus aria-hidden="true" /> Add account</Link>
        ) : null}
      </section>

      <section className="client-accounts-panel" aria-label="Account directory">
        <div className="client-list-heading">
          <div>
            <h2>Account directory</h2>
            <p className="secondary">Searches and pages are scoped by the authenticated workspace.</p>
          </div>
          <div className="client-search">
            <Input
              label="Search accounts"
              type="search"
              placeholder="Company or domain"
              value={search}
              onChange={(event) => changeSearch(event.target.value)}
            />
          </div>
        </div>

        {page.error ? (
          <div className="client-data-state error-box" role="alert">
            <strong>Accounts could not be loaded</strong>
            <p>{page.error.detail}</p>
            <button className="btn" type="button" onClick={page.reload}>Try again</button>
          </div>
        ) : (
          <>
            {page.data?.accounts.length === 0 && !search.trim() ? (
              <EmptyState
                title="Start with your first account"
                description="Add a target company to begin collecting sourced account research."
                action={canAdd ? <Link className="btn primary" href="/#add-account">Add account</Link> : undefined}
              />
            ) : (
              <DataTable
                caption="Workspace accounts"
                columns={columns}
                rows={page.data?.accounts ?? []}
                rowKey={(account) => account.domain}
                loading={!page.data}
                emptyMessage="No accounts match this search."
              />
            )}
            {page.data ? (
              <div className="client-pagination" aria-label="Account pages">
                <span className="muted">{page.data.accounts.length} accounts on this page</span>
                <div className="design-swatch-row">
                  <button
                    className="btn sm"
                    type="button"
                    disabled={history.length === 0}
                    onClick={() => {
                      const nextHistory = [...history];
                      const previous = nextHistory.pop() ?? null;
                      setHistory(nextHistory);
                      setCursor(previous);
                    }}
                  >
                    Previous
                  </button>
                  <button
                    className="btn sm"
                    type="button"
                    disabled={!page.data.next_after}
                    onClick={() => {
                      if (!page.data?.next_after) return;
                      setHistory((items) => [...items, cursor]);
                      setCursor(page.data.next_after);
                    }}
                  >
                    Next
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}

export default function AccountsPage() {
  return <SignedIn>{(session) => <AccountList canAdd={session.role !== "viewer"} />}</SignedIn>;
}
