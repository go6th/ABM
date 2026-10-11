"use client";
// Accounts as rows: name, one line of context, research state and when. Search and sort run on
// the list the server already returned; the URL holds the query so a link reproduces the view.
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import type { AccountItem } from "@/lib/gen/api.gen";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { m } from "@/lib/messages";
import { ago, runState } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

type Sort = "recent" | "name";

function stateLine(a: AccountItem): { text: string; tone: "" | "running" | "failed" } {
  const state = runState(a.latest_run?.status);
  if (state === "running") return { text: m.accounts.running, tone: "running" };
  if (state === "failed") return { text: m.accounts.failed, tone: "failed" };
  if (state === "done") return { text: m.accounts.refreshed(ago(a.latest_run?.created_at)), tone: "" };
  return { text: m.accounts.neverRefreshed, tone: "" };
}

function AddAccount({ atCap }: { atCap: boolean }) {
  const router = useRouter();
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const value = domain.trim().toLowerCase();
    if (!value || busy || atCap) return;
    setBusy(true);
    setError(null);
    try {
      const added = await api<{ domain: string }>("POST", "/v1/accounts", { domain: value }, { "Idempotency-Key": newIdempotencyKey() });
      router.push(`/accounts/${encodeURIComponent(added.domain)}`);
      router.refresh();
    } catch (cause) {
      setBusy(false);
      if (cause instanceof ApiError && (cause.status === 402 || cause.status === 409 || cause.type.includes("limit"))) setError(m.cap.full);
      else if (cause instanceof ApiError && cause.status === 422) setError(m.addAccount.invalid);
      else setError(m.addAccount.failed);
    }
  };
  return (
    <form className="ws-add" id="add" onSubmit={(e) => void submit(e)} aria-labelledby="add-title">
      <h2 id="add-title" className="ws-label">{m.nav.add}</h2>
      <div className="line">
        <label className="ws-sr" htmlFor="add-domain">{m.addAccount.label}</label>
        <input
          id="add-domain" className="ws-input" inputMode="url" autoComplete="off" spellCheck={false}
          placeholder={m.addAccount.placeholder} value={domain} disabled={atCap}
          onChange={(e) => setDomain(e.target.value)}
        />
        <button className="ws-btn primary" type="submit" disabled={atCap || busy || !domain.trim()}>
          <Plus size={16} strokeWidth={1.8} aria-hidden="true" />{m.nav.add}
        </button>
      </div>
      {atCap ? <p className="ws-small ws-muted">{m.cap.full}</p> : <p className="ws-small ws-muted">{m.addAccount.hint}</p>}
      {error ? <p className="ws-small ws-error" role="alert">{error}</p> : null}
    </form>
  );
}

export function AccountList({ accounts, atCap, canAdd }: { accounts: AccountItem[]; atCap: boolean; canAdd: boolean }) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const sort: Sort = params.get("sort") === "name" ? "name" : "recent";
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value); else next.delete(key);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = accounts.filter((a) => !q || a.domain.includes(q) || (a.name ?? "").toLowerCase().includes(q));
    return [...list].sort((a, b) => sort === "name"
      ? (a.name ?? a.domain).localeCompare(b.name ?? b.domain)
      : (b.latest_run?.created_at ?? "").localeCompare(a.latest_run?.created_at ?? "") || a.domain.localeCompare(b.domain));
  }, [accounts, query, sort]);

  return (
    <>
      {accounts.length > 0 ? (
        <div className="ws-toolbar">
          <div className="ws-search">
            <Search size={16} strokeWidth={1.6} aria-hidden="true" />
            <input
              className="ws-input" type="search" aria-label={m.accounts.search} placeholder={m.accounts.search}
              value={query} onChange={(e) => { setQuery(e.target.value); setParam("q", e.target.value || null); }}
            />
          </div>
          <div className="ws-seg" role="group" aria-label={m.accounts.sortLabel}>
            <button type="button" aria-pressed={sort === "recent"} onClick={() => setParam("sort", null)}>{m.accounts.sortRecent}</button>
            <button type="button" aria-pressed={sort === "name"} onClick={() => setParam("sort", "name")}>{m.accounts.sortName}</button>
          </div>
        </div>
      ) : null}
      {accounts.length === 0 ? (
        <div className="ws-empty"><h3>{m.accounts.emptyTitle}</h3><p>{m.accounts.emptyBody}</p></div>
      ) : shown.length === 0 ? (
        <p className="ws-muted">{m.accounts.noMatch(query.trim())}</p>
      ) : (
        <ul className="ws-rows">
          {shown.map((a) => {
            const line = stateLine(a);
            return (
              <li key={a.domain}>
                <Link className="ws-row" href={`/accounts/${encodeURIComponent(a.domain)}`}>
                  <div>
                    <p className="name">{cellText(a.name ?? a.domain)}</p>
                    <p className="context">{a.name ? cellText(a.domain) : m.accounts.noProfile}</p>
                  </div>
                  <div className="meta">
                    <span className={line.tone ? `ws-state ${line.tone}` : "ws-state"}>{line.text}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {canAdd ? <AddAccount atCap={atCap} /> : null}
    </>
  );
}
