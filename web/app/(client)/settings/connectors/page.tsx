"use client";
// Client history connectors (WS6, D-65): per-source consent by an admin, folders and objects
// allowed by the admin, items found outside them held off limits until an admin clears them, and
// a bounded fetch now. Content fetched from a connector never appears in client-facing tables
// or files; this page shows counts and identifiers only.
import { useState } from "react";
import { Cable, FolderPlus, RefreshCw } from "lucide-react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { humanize } from "@/lib/ws-format";
import { ConfirmButton } from "@/ui/ConfirmButton";
import { Badge, Banner, EmptyState, LoadError, PageHead, PageSkeleton, Section } from "@/ui/primitives";

interface Connection { id: string; kind: string; status: string; scopes: string[]; allowed: string[]; quarantined: number; last_fetch_at: string | null }
interface Held { id: string; resource_id: string; reason: string }
interface Fetched { fetched: number; held: number; skipped: number; pages: number; exit_reason: string | null }

const KINDS = ["google_drive", "salesforce", "hubspot"] as const;
const SOURCE_NAME: Record<string, string> = { google_drive: "Google Drive", salesforce: "Salesforce", hubspot: "HubSpot" };
type GrantKind = { value: "drive_folder" | "crm_object" | "crm_field"; label: string };
const DRIVE_GRANTS: GrantKind[] = [{ value: "drive_folder", label: "Folder" }];
const GRANT_KINDS: Record<string, GrantKind[]> = {
  google_drive: [{ value: "drive_folder", label: "Folder" }],
  salesforce: [{ value: "crm_object", label: "Object" }, { value: "crm_field", label: "Field" }],
  hubspot: [{ value: "crm_object", label: "Object" }, { value: "crm_field", label: "Field" }],
};

function Allow({ connection, onDone, onError }: { connection: Connection; onDone: (text: string) => void; onError: (err: unknown) => void }) {
  const kinds = GRANT_KINDS[connection.kind] ?? DRIVE_GRANTS;
  const [kind, setKind] = useState<GrantKind["value"]>(kinds[0]?.value ?? "drive_folder");
  const [resource, setResource] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resource.trim()) return;
    setBusy(true);
    try {
      await api("POST", `/v1/connectors/${encodeURIComponent(connection.id)}/grants`, { grant_kind: kind, resource_id: resource.trim() });
      setResource("");
      onDone(`Allowed ${resource.trim()}.`);
    } catch (err) { onError(err); }
    finally { setBusy(false); }
  };
  return (
    <form className="form-row" onSubmit={(e) => void submit(e)} aria-label={`Allow a source for ${SOURCE_NAME[connection.kind] ?? connection.kind}`}>
      {kinds.length > 1 ? (
        <div className="field">
          <label htmlFor={`gk-${connection.id}`}>Kind</label>
          <select id={`gk-${connection.id}`} className="select" value={kind} onChange={(e) => setKind(e.target.value as GrantKind["value"])}>{kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select>
        </div>
      ) : null}
      <div className="field">
        <label htmlFor={`gr-${connection.id}`}>{kinds.length > 1 ? "Identifier" : "Folder ID"}</label>
        <input id={`gr-${connection.id}`} className="input mono" maxLength={300} pattern="[A-Za-z0-9_.:\-]+" value={resource} onChange={(e) => setResource(e.target.value)} />
      </div>
      <button className="btn" type="submit" disabled={busy || !resource.trim()}><FolderPlus size={16} aria-hidden="true" />Allow</button>
    </form>
  );
}

function Connectors({ s }: { s: Session }) {
  const loaded = useApi<{ connections: Connection[] }>(s.role === "admin" ? "/v1/connectors" : null);
  const list = loaded.data?.connections ?? [];
  const [held, setHeld] = useState<Record<string, Held[]>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = loaded.reload;
  if (s.role !== "admin") return <div className="content"><PageHead title="Integrations" /><Banner kind="neutral">Only admins manage integrations.</Banner></div>;
  const report = (err: unknown) => setMsg({ ok: false, text: err instanceof ApiError ? `Request refused (${err.type}).` : "Request refused." });
  const connect = async (kind: string) => {
    setBusy(`connect:${kind}`); setMsg(null);
    try {
      const b = await api<{ authorize_url: string }>("POST", "/v1/connectors", { kind });
      window.location.assign(b.authorize_url);
    } catch (err) { report(err); setBusy(null); }
  };
  const showHeld = async (id: string) => {
    setBusy(`review:${id}`); setMsg(null);
    try { const b = await api<{ items: Held[] }>("GET", `/v1/connectors/${encodeURIComponent(id)}/quarantine`); setHeld((h) => ({ ...h, [id]: b.items })); }
    catch (err) { report(err); }
    finally { setBusy(null); }
  };
  const clear = async (id: string, item: string) => {
    setBusy(`clear:${item}`); setMsg(null);
    try {
      await api("POST", `/v1/connectors/${encodeURIComponent(id)}/quarantine/${encodeURIComponent(item)}/clear`, {});
      const b = await api<{ items: Held[] }>("GET", `/v1/connectors/${encodeURIComponent(id)}/quarantine`);
      setHeld((h) => ({ ...h, [id]: b.items }));
      setMsg({ ok: true, text: "The held item was cleared." });
      load();
    } catch (err) { report(err); }
    finally { setBusy(null); }
  };
  const fetchNow = async (connection: Connection) => {
    setBusy(`fetch:${connection.id}`); setMsg(null);
    try {
      const r = await api<Fetched>("POST", `/v1/connectors/${encodeURIComponent(connection.id)}/fetch`, {});
      setMsg({ ok: true, text: `Fetched ${r.fetched}, held ${r.held} outside allowed sources, skipped ${r.skipped}.` });
      load();
    } catch (err) { report(err); }
    finally { setBusy(null); }
  };
  const disconnect = async (connection: Connection) => {
    setBusy(`disconnect:${connection.id}`); setMsg(null);
    try {
      await api("POST", `/v1/connectors/${encodeURIComponent(connection.id)}/disconnect`, {});
      setMsg({ ok: true, text: "Connector disconnected." });
      load();
    } catch (err) { report(err); }
    finally { setBusy(null); }
  };
  return (
    <div className="content">
      <PageHead title="Integrations" sub="Connect your own sources. Only folders and objects you allow are read; anything else is held until you clear it." />
      {msg ? <p className={msg.ok ? "inline-status block-gap" : "inline-error block-gap"} role={msg.ok ? "status" : "alert"}>{msg.text}</p> : null}
      <Section title="Connect a source">
        <div className="row">
          {KINDS.map((kind) => (
            <button className="btn" key={kind} type="button" disabled={busy !== null} onClick={() => void connect(kind)}>
              <Cable size={16} aria-hidden="true" />{busy === `connect:${kind}` ? "Connecting" : `Connect ${SOURCE_NAME[kind]}`}
            </button>
          ))}
        </div>
      </Section>
      <Section title="Connected sources">
        {loaded.error ? <LoadError reference={null} retryHref="/settings/connectors" />
          : !loaded.data ? <PageSkeleton />
            : list.length === 0 ? <EmptyState title="No connected sources" body="Connect a source above. You then choose which folders or objects it may read." icon={<Cable size={20} strokeWidth={1.8} />} />
              : (
                <ul className="stack" aria-label="Connected sources">
                  {list.map((connection) => (
                    <li key={connection.id} className="card stack">
                      <div className="row between">
                        <div>
                          <p className="card-title">{SOURCE_NAME[connection.kind] ?? humanize(connection.kind)}</p>
                          <p className="card-sub">{connection.last_fetch_at ? `Last fetched ${new Date(connection.last_fetch_at).toLocaleString()}` : "Not fetched yet"}</p>
                        </div>
                        <div className="row">
                          <Badge tone={connection.status === "active" ? "mint" : "butter"} dot>{humanize(connection.status)}</Badge>
                          <button className="btn sm" type="button" disabled={busy !== null} onClick={() => void fetchNow(connection)}>
                            <RefreshCw size={14} aria-hidden="true" />{busy === `fetch:${connection.id}` ? "Fetching" : "Fetch now"}
                          </button>
                          <ConfirmButton label="Disconnect" size="sm" tone="danger" disabled={busy !== null} title={`Disconnect ${SOURCE_NAME[connection.kind] ?? connection.kind}?`} body="Access to that source stops and its token is removed." action="Disconnect" onConfirm={() => void disconnect(connection)} />
                        </div>
                      </div>
                      <dl className="dl">
                        <dt>Allowed sources</dt><dd className="mono t-footnote">{connection.allowed.map((a) => cellText(a)).join(", ") || "None yet"}</dd>
                        <dt>Held items</dt>
                        <dd className="row">
                          <span className="num">{connection.quarantined}</span>
                          {connection.quarantined > 0 ? (
                            <button className="btn sm quiet" type="button" disabled={busy !== null} onClick={() => void showHeld(connection.id)}>
                              {busy === `review:${connection.id}` ? "Loading" : "Review"}
                            </button>
                          ) : null}
                        </dd>
                      </dl>
                      {(held[connection.id] ?? []).length ? (
                        <ul className="group" aria-label="Held items">
                          {(held[connection.id] ?? []).map((item) => (
                            <li key={item.id} className="group-item">
                              <span className="grow"><span className="mono t-footnote">{cellText(item.resource_id)}</span><br /><span className="sub">{humanize(cellText(item.reason))}</span></span>
                              <button className="btn sm" type="button" disabled={busy !== null} onClick={() => void clear(connection.id, item.id)}>Clear</button>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      <Allow connection={connection} onDone={(text) => { setMsg({ ok: true, text }); load(); }} onError={report} />
                    </li>
                  ))}
                </ul>
              )}
      </Section>
    </div>
  );
}

export default function ConnectorsPage() { return <SignedIn>{(s) => <Connectors s={s} />}</SignedIn>; }
