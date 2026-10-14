"use client";
// Mail and drive (screen 8): the Mail and Drive cards with their status pill and Connect or
// Disconnect, and when connected what was matched, the last sync and "Shown only to: You". A
// source with no registered OAuth app says "Not available yet" instead of offering a Connect
// that cannot work. Connect sends the browser to the provider's consent screen (read-only
// scopes); Disconnect asks first, then revokes and deletes the synced items.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { MailAndDrive, MailDriveConnect, MailDriveDisconnected } from "@/lib/gen/api.gen";

type Family = MailAndDrive["mail"];

const CARD = {
  mail: { title: "Mail", sub: "Gmail or Outlook. Threads that mention your accounts.", noun: ["thread", "threads"] },
  drive: { title: "Drive", sub: "Google Drive, OneDrive or SharePoint. Proposals, notes and decks.", noun: ["file", "files"] },
} as const;

function synced(at: string | null): string {
  if (!at) return "Not yet";
  const when = new Date(at);
  const time = when.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  if (when.toDateString() === today.toDateString()) return `Today, ${time}`;
  if (when.toDateString() === yesterday.toDateString()) return `Yesterday, ${time}`;
  return `${when.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${time}`;
}

function plural(n: number, [one, many]: readonly [string, string]): string {
  return `${n} ${n === 1 ? one : many}`;
}

function Card({ family, data }: { family: "mail" | "drive"; data: Family }) {
  const router = useRouter();
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const copy = CARD[family];
  const available = data.options.filter((o) => o.available);
  const connected = data.status === "connected";
  const broken = data.status === "broken";

  const connect = async (kind: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const started = await api<MailDriveConnect>("POST", `/v1/mail-and-drive/${kind}/connect`);
      window.location.assign(started.authorize_url);
    } catch (cause) {
      setMessage(cause instanceof ApiError && cause.detail ? cause.detail : "The connection could not be started. Try again.");
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm(`Disconnect ${data.source ?? copy.title}? The synced items are removed from your workspace.`)) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<MailDriveDisconnected>("POST", `/v1/mail-and-drive/${family}/disconnect`);
      router.refresh();
    } catch {
      setMessage("It could not be disconnected. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const pill = connected ? <span className="pill ok g">Connected</span>
    : broken ? <span className="pill warn g">Needs reconnecting</span>
      : data.status === "not_available" ? <span className="pill neutral g">Not available yet</span>
        : <span className="pill neutral g">Not connected</span>;

  return (
    <section className="card pad stack-16" aria-labelledby={`${family}-title`}>
      <div className="row-16 wrap">
        <div className="grow-240">
          <div className="card-title" id={`${family}-title`}>{copy.title}</div>
          <div className="mut sm-text mt-2">{connected && data.source ? `${data.source}. ` : ""}{copy.sub}</div>
        </div>
        {pill}
        {connected ? (
          <button type="button" className="btn" disabled={busy} onClick={() => void disconnect()}>Disconnect</button>
        ) : data.status === "not_available" ? (
          <button type="button" className="btn" disabled>Not available yet</button>
        ) : available.length === 1 && !choosing ? (
          <button type="button" className="btn primary" disabled={busy} onClick={() => void connect(available[0]!.kind)}>
            {broken ? "Reconnect" : "Connect"}
          </button>
        ) : (
          <button type="button" className="btn primary" disabled={busy} aria-expanded={choosing} onClick={() => setChoosing((v) => !v)}>
            {broken ? "Reconnect" : "Connect"}
          </button>
        )}
      </div>
      {choosing && !connected ? (
        <div className="row-12 wrap" role="group" aria-label={`Choose your ${copy.title.toLowerCase()} source`}>
          {data.options.map((o) => (
            <button key={o.kind} type="button" className="btn sm" disabled={!o.available || busy} onClick={() => void connect(o.kind)}>
              {o.available ? `Connect ${o.label}` : `${o.label}: not available yet`}
            </button>
          ))}
        </div>
      ) : null}
      {data.status === "not_available" ? (
        <p className="mut sm-text">Your administrator has not set up {copy.title.toLowerCase()} connections yet.</p>
      ) : null}
      {broken ? <p className="mut sm-text">Access was withdrawn at the source. Reconnect to keep matching new items.</p> : null}
      {message ? <p className="sm-text bad-text" role="alert">{message}</p> : null}
      {connected ? (
        <dl className="kv-list line-top">
          <div><dt className="mut">Matched to your accounts</dt><dd>{plural(data.accounts, ["account", "accounts"])} · {plural(data.items, copy.noun)}</dd></div>
          <div><dt className="mut">Last synced</dt><dd>{synced(data.last_synced_at)}</dd></div>
          <div><dt className="mut">Shown only to</dt><dd>You</dd></div>
        </dl>
      ) : null}
    </section>
  );
}

export function MailDrive({ view }: { view: MailAndDrive }) {
  return (
    <>
      <Card family="mail" data={view.mail} />
      <Card family="drive" data={view.drive} />
      <section className="card quiet pad-sm stack-8">
        <div className="lbl">How it is used</div>
        <p className="body-text">
          Mail and files are labelled <b>From your mail and drive</b> wherever they appear, and sit in their own section on each account. They are never mixed into the research files you download. You can disconnect at any time and the synced items are removed.
        </p>
      </section>
    </>
  );
}
