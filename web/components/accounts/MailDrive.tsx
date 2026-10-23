"use client";
// Mail and drive (screen 8): the user's own read-only sources, grouped by provider (Google:
// Gmail and Google Drive; Microsoft: Outlook, OneDrive and SharePoint), showing only what the API
// returns. Each card has its status pill (Connected, Needs attention, Not connected, Not available
// yet), when it last synced, how many items matched the user's accounts, the last error as a
// plain sentence (the API sends a code, never provider text), and Connect, Reconnect, Sync now
// (result shown inline) and Disconnect (asked first; revokes and deletes the synced items).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { MailAndDrive, MailDriveConnect, MailDriveDisconnected } from "@/lib/gen/api.gen";
import { m } from "@/lib/messages";
import { useIsClient } from "@/lib/use-is-client";

const t = m.mailDrive;

// The per-source fields are newer than the generated client (web/lib/gen/api.gen.ts); they are
// typed here until it is regenerated.
export type ProviderFamily = "google" | "microsoft";
export type SourceStatus = "connected" | "not_connected" | "not_available" | "broken";
export interface MailDriveSourceView {
  kind: string;
  label: string;
  family: ProviderFamily;
  group: "mail" | "drive";
  available: boolean;
  status: SourceStatus;
  accounts: number;
  items: number;
  last_synced_at: string | null;
  broken_reason?: string | null;
  last_sync_status?: "ok" | "partial" | "failed" | null;
  last_error?: string | null;
  last_error_at?: string | null;
  last_sync_listed?: number | null;
  last_sync_matched?: number | null;
}
export type MailDriveView = MailAndDrive & { sources?: MailDriveSourceView[] };
interface SyncedSource { kind: string; state: "ok" | "partial" | "failed" | "broken" | "skipped"; matched: number; reason: string | null }
interface Synced { family: ProviderFamily; sources: SyncedSource[] }

const FAMILIES: ProviderFamily[] = ["google", "microsoft"];
const FAMILY_OF: Record<string, ProviderFamily> = {
  gmail: "google", google_drive: "google", outlook: "microsoft", onedrive: "microsoft", sharepoint: "microsoft",
};
const RECONNECT = new Set(["token_revoked", "scope_changed", "scope_mismatch", "no_refresh_token", "token_missing", "consent_refused"]);

/** The sources the API sent; an older API without ``sources`` is read from its two cards. */
export function sourcesOf(view: MailDriveView): MailDriveSourceView[] {
  if (view.sources && view.sources.length) return view.sources;
  return [view.mail, view.drive].flatMap((card, i) => card.options.map((o): MailDriveSourceView => {
    const mine = card.kind === o.kind;
    return {
      kind: o.kind,
      label: o.label,
      family: FAMILY_OF[o.kind] ?? "google",
      group: i === 0 ? "mail" : "drive",
      available: o.available,
      status: mine ? (card.status as SourceStatus) : o.available ? "not_connected" : "not_available",
      accounts: mine ? card.accounts : 0,
      items: mine ? card.items : 0,
      last_synced_at: mine ? card.last_synced_at : null,
    };
  }));
}

/** A stored error or reason code as a plain sentence for this provider. */
export function errorSentence(code: string, family: ProviderFamily): string {
  if (code === "token_revoked") return t.errors.revoked(t.groups[family]);
  if (code === "scope_changed" || code === "scope_mismatch" || code === "no_refresh_token") return t.errors.scope;
  if (code === "token_refresh_failed" || code === "provider_unavailable") return t.errors.provider;
  if (code === "rate_limited") return t.errors.rateLimited;
  if (code === "token_missing") return t.errors.missing;
  return t.errors.other;
}

/** "just now", "5 minutes ago", "3 hours ago", "yesterday", "4 days ago", then a date. */
export function relative(iso: string, now = new Date()): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  const minutes = Math.floor((now.getTime() - when.getTime()) / 60_000);
  if (minutes < 1) return t.when.justNow;
  if (minutes < 60) return t.when.minutes(minutes);
  if (minutes < 24 * 60) return t.when.hours(Math.floor(minutes / 60));
  const days = Math.floor(minutes / (24 * 60));
  if (days === 1) return m.when.yesterday;
  if (days < 7) return m.when.daysAgo(days);
  return when.toLocaleDateString("en-GB", { day: "numeric", month: "long" });
}

function Icon({ group }: { group: "mail" | "drive" }) {
  return group === "mail" ? (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <path d="M4 7l8 6 8-6" />
    </svg>
  ) : (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2.5h6.5A2.5 2.5 0 0 1 21 10v6.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z" />
    </svg>
  );
}

function Pill({ status }: { status: SourceStatus }) {
  const tone = status === "connected" ? "ok" : status === "broken" ? "warn" : "neutral";
  return <span className={`pill ${tone} g`}>{t.status[status]}</span>;
}

export function SourceCard({ source }: { source: MailDriveSourceView }) {
  const router = useRouter();
  const client = useIsClient(); // relative times are computed in the browser only
  const [busy, setBusy] = useState<"connect" | "sync" | "disconnect" | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const { status, family } = source;
  const live = status === "connected" || status === "broken";
  // a broken grant says why; otherwise the last error, until a sync finishes cleanly again
  const problem = status === "broken" ? source.broken_reason || "token_revoked"
    : source.last_sync_status === "ok" ? null : source.last_error || null;
  const reconnect = status === "broken" || (problem !== null && RECONNECT.has(problem));

  const connect = async () => {
    setBusy("connect");
    setResult(null);
    try {
      const started = await api<MailDriveConnect>("POST", `/v1/mail-and-drive/${source.kind}/connect`);
      window.location.assign(started.authorize_url);
    } catch (cause) {
      setResult({ ok: false, text: cause instanceof ApiError && cause.detail ? cause.detail : t.connectFailed });
      setBusy(null);
    }
  };

  const syncNow = async () => {
    setBusy("sync");
    setResult(null);
    try {
      const done = await api<Synced>("POST", `/v1/mail-and-drive/${family}/sync`);
      const mine = done.sources.find((s) => s.kind === source.kind);
      if (!mine || mine.state === "ok" || mine.state === "partial") setResult({ ok: true, text: t.found(mine?.matched ?? 0) });
      else if (mine.state === "skipped") setResult({ ok: false, text: t.syncSkipped });
      else setResult({ ok: false, text: errorSentence(mine.reason ?? "sync_failed", family) });
      router.refresh();
    } catch (cause) {
      const text = cause instanceof ApiError && cause.status === 429 ? t.syncTooSoon
        : cause instanceof ApiError && cause.status === 403 ? t.turnedOff : t.syncFailed;
      setResult({ ok: false, text });
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    if (!window.confirm(t.confirmDisconnect(source.label))) return;
    setBusy("disconnect");
    setResult(null);
    try {
      await api<MailDriveDisconnected>("POST", `/v1/mail-and-drive/${source.kind}/disconnect`);
      router.refresh();
    } catch {
      setResult({ ok: false, text: t.disconnectFailed });
    } finally {
      setBusy(null);
    }
  };

  const titleId = `md-${source.kind}`;
  return (
    <section className="card pad md-card" aria-labelledby={titleId}>
      <div className="md-head">
        <span className={`md-icon ${family}`}><Icon group={source.group} /></span>
        <div className="md-name" id={titleId}>{source.label}</div>
        <Pill status={status} />
      </div>
      {status === "not_available" ? <p className="mut sm-text">{t.notAvailable}</p> : null}
      {live ? (
        <ul className="md-facts">
          <li>{source.last_synced_at ? (client ? t.lastSynced(relative(source.last_synced_at)) : "") : t.neverSynced}</li>
          <li>{t.matched(source.items)}</li>
          <li>{t.shownTo}</li>
        </ul>
      ) : null}
      {live && problem ? <p className="sm-text bad-text" role="alert">{errorSentence(problem, family)}</p>
        : live && source.last_sync_status === "partial" ? <p className="mut sm-text">{t.partial}</p> : null}
      {result ? (
        <p className={`sm-text ${result.ok ? "md-ok" : "bad-text"}`} role={result.ok ? "status" : "alert"}>{result.text}</p>
      ) : null}
      {status === "not_available" ? null : (
        <div className="md-actions">
          {status === "not_connected" ? (
            <button type="button" className="btn primary sm" disabled={busy !== null} onClick={() => void connect()}>{t.connect}</button>
          ) : null}
          {live && reconnect ? (
            <button type="button" className="btn primary sm" disabled={busy !== null} onClick={() => void connect()}>{t.reconnect}</button>
          ) : null}
          {status === "connected" ? (
            <button type="button" className="btn sm" disabled={busy !== null} onClick={() => void syncNow()}>
              {busy === "sync" ? t.syncing : t.syncNow}
            </button>
          ) : null}
          {live ? (
            <button type="button" className="btn sm danger" disabled={busy !== null} onClick={() => void disconnect()}>{t.disconnect}</button>
          ) : null}
        </div>
      )}
    </section>
  );
}

export function MailDrive({ view }: { view: MailDriveView }) {
  const sources = sourcesOf(view);
  return (
    <div className="md-groups">
      <section className="card quiet pad-sm md-privacy">
        <span className="pill internal g">{t.shownTo}</span>
        <p className="sm-text">{t.privacy} <Link href="/privacy">How we handle mail and drive</Link></p>
      </section>
      {FAMILIES.map((family) => {
        const mine = sources.filter((s) => s.family === family);
        if (!mine.length) return null;
        return (
          <section key={family} className="md-group" aria-labelledby={`md-group-${family}`}>
            <div>
              <h2 className="card-title" id={`md-group-${family}`}>{t.groups[family]}</h2>
              <p className="mut sm-text">{t.groupSub[family]}</p>
            </div>
            <div className="md-grid">
              {mine.map((s) => <SourceCard key={s.kind} source={s} />)}
            </div>
          </section>
        );
      })}
    </div>
  );
}
