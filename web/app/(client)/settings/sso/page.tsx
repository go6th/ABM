"use client";
// The workspace's identity provider connections (admins): list, create and edit. The client
// secret is write-only: it is sent when typed and never shown. A SAML certificate is not
// returned by the service, so an edit of a SAML connection asks for it again.
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { KeyRound, Pencil, Plus, X } from "lucide-react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { useApi } from "@/lib/useApi";
import { cellText } from "@/lib/render";
import { humanize } from "@/lib/ws-format";
import { Badge, Banner, EmptyState, LoadError, PageHead, PageSkeleton } from "@/ui/primitives";

interface Conn {
  id: string; slug: string; protocol: "oidc" | "saml"; issuer: string; client_id: string | null; authorization_endpoint: string | null;
  token_endpoint: string | null; jwks_uri: string | null; saml_sso_url: string | null; sp_entity_id: string | null;
  verified_domains: string[]; group_role_map: Record<string, string>; jit_enabled: boolean; require_admin_mfa: boolean;
  status: string; version: number; has_client_secret: boolean;
}

const ROLES = ["admin", "analyst", "seller", "viewer"];
const lines = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);

function parseGroups(text: string): Record<string, string> | string {
  const map: Record<string, string> = {};
  for (const line of lines(text)) {
    const at = line.lastIndexOf(":");
    if (at < 1) return `Write each mapping as group: role (${line}).`;
    const group = line.slice(0, at).trim();
    const role = line.slice(at + 1).trim().toLowerCase();
    if (!ROLES.includes(role)) return `${role || "That"} is not a role. Use admin, analyst, seller or viewer.`;
    map[group] = role;
  }
  return map;
}

function ConnectionForm({ conn, onDone }: { conn: Conn | null; onDone: (text: string) => void }) {
  const [protocol, setProtocol] = useState<"oidc" | "saml">(conn?.protocol ?? "oidc");
  const [slug, setSlug] = useState(conn?.slug ?? "");
  const [issuer, setIssuer] = useState(conn?.issuer ?? "");
  const [clientId, setClientId] = useState(conn?.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [authz, setAuthz] = useState(conn?.authorization_endpoint ?? "");
  const [token, setToken] = useState(conn?.token_endpoint ?? "");
  const [jwks, setJwks] = useState(conn?.jwks_uri ?? "");
  const [ssoUrl, setSsoUrl] = useState(conn?.saml_sso_url ?? "");
  const [cert, setCert] = useState("");
  const [spEntity, setSpEntity] = useState(conn?.sp_entity_id ?? "");
  const [domains, setDomains] = useState((conn?.verified_domains ?? []).join("\n"));
  const [groups, setGroups] = useState(Object.entries(conn?.group_role_map ?? {}).map(([g, r]) => `${g}: ${r}`).join("\n"));
  const [jit, setJit] = useState(conn?.jit_enabled ?? false);
  const [mfa, setMfa] = useState(conn?.require_admin_mfa ?? true);
  const [active, setActive] = useState(conn ? conn.status === "active" : true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const opt = (v: string) => (v.trim() ? v.trim() : null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const map = parseGroups(groups);
    if (typeof map === "string") { setError(map); return; }
    setBusy(true);
    setError(null);
    const body = {
      slug: slug.trim(), protocol, issuer: issuer.trim(),
      client_id: protocol === "oidc" ? opt(clientId) : null,
      ...(secret ? { client_secret: secret } : {}),
      authorization_endpoint: protocol === "oidc" ? opt(authz) : null,
      token_endpoint: protocol === "oidc" ? opt(token) : null,
      jwks_uri: protocol === "oidc" ? opt(jwks) : null,
      saml_sso_url: protocol === "saml" ? opt(ssoUrl) : null,
      saml_idp_cert_pem: protocol === "saml" ? opt(cert) : null,
      sp_entity_id: protocol === "saml" ? opt(spEntity) : null,
      verified_domains: lines(domains).map((d) => d.toLowerCase()),
      group_role_map: map, jit_enabled: jit, require_admin_mfa: mfa,
    };
    try {
      if (conn) await api("PUT", `/v1/sso/connections/${encodeURIComponent(conn.id)}`, { ...body, expected_version: conn.version, status: active ? "active" : "disabled" });
      else await api("POST", "/v1/sso/connections", body);
      onDone(conn ? "Connection saved." : "Connection created.");
    } catch (err) {
      setError(err instanceof ApiError
        ? err.type === "step_up_required" ? "Sign in again with multi-factor authentication to change sign-in settings."
          : err.status === 409 ? "This connection changed since you opened it. Close and open it again." : `The connection was refused (${err.type}).`
        : "The connection was refused.");
    } finally { setBusy(false); }
  };
  return (
    <form className="form" onSubmit={(e) => void submit(e)}>
      <div className="field">
        <span className="label">Protocol</span>
        <div className="seg" role="group" aria-label="Protocol">
          {(["oidc", "saml"] as const).map((p) => <button key={p} type="button" aria-pressed={protocol === p} disabled={conn !== null} onClick={() => setProtocol(p)}>{p === "oidc" ? "OpenID Connect" : "SAML"}</button>)}
        </div>
      </div>
      <div className="grid-2">
        <div className="field"><label htmlFor="c-slug">Sign-in ID</label><input id="c-slug" className="input mono" required pattern="[a-z0-9][a-z0-9-]{2,62}" value={slug} onChange={(e) => setSlug(e.target.value)} aria-describedby="c-slug-hint" /><p className="hint" id="c-slug-hint">Lowercase letters, digits and hyphens. People type it to sign in.</p></div>
        <div className="field"><label htmlFor="c-issuer">Issuer</label><input id="c-issuer" className="input mono" required maxLength={500} value={issuer} onChange={(e) => setIssuer(e.target.value)} /></div>
      </div>
      {protocol === "oidc" ? (
        <>
          <div className="grid-2">
            <div className="field"><label htmlFor="c-client">Client ID</label><input id="c-client" className="input mono" maxLength={300} value={clientId} onChange={(e) => setClientId(e.target.value)} /></div>
            <div className="field">
              <label htmlFor="c-secret">Client secret</label>
              <input id="c-secret" className="input" type="password" autoComplete="off" minLength={8} maxLength={500} value={secret} onChange={(e) => setSecret(e.target.value)} aria-describedby="c-secret-hint" />
              <p className="hint" id="c-secret-hint">{conn?.has_client_secret ? "A secret is stored. Leave empty to keep it." : "Stored encrypted and never shown again."}</p>
            </div>
          </div>
          <div className="field"><label htmlFor="c-authz">Authorization endpoint</label><input id="c-authz" className="input mono" type="url" pattern="https://.*" value={authz} onChange={(e) => setAuthz(e.target.value)} /></div>
          <div className="grid-2">
            <div className="field"><label htmlFor="c-token">Token endpoint</label><input id="c-token" className="input mono" type="url" pattern="https://.*" value={token} onChange={(e) => setToken(e.target.value)} /></div>
            <div className="field"><label htmlFor="c-jwks">Key set address</label><input id="c-jwks" className="input mono" type="url" pattern="https://.*" value={jwks} onChange={(e) => setJwks(e.target.value)} /></div>
          </div>
        </>
      ) : (
        <>
          <div className="grid-2">
            <div className="field"><label htmlFor="c-sso">Sign-in address</label><input id="c-sso" className="input mono" type="url" pattern="https://.*" value={ssoUrl} onChange={(e) => setSsoUrl(e.target.value)} /></div>
            <div className="field"><label htmlFor="c-sp">Service provider entity ID</label><input id="c-sp" className="input mono" maxLength={500} value={spEntity} onChange={(e) => setSpEntity(e.target.value)} /></div>
          </div>
          <div className="field">
            <label htmlFor="c-cert">Identity provider certificate</label>
            <textarea id="c-cert" className="textarea mono" rows={4} required={conn !== null} maxLength={20000} value={cert} onChange={(e) => setCert(e.target.value)} aria-describedby="c-cert-hint" />
            <p className="hint" id="c-cert-hint">PEM format.{conn ? " The stored certificate is not shown, so paste it again to save changes." : ""}</p>
          </div>
        </>
      )}
      <div className="grid-2">
        <div className="field"><label htmlFor="c-groups">Groups to roles</label><textarea id="c-groups" className="textarea mono" rows={4} value={groups} onChange={(e) => setGroups(e.target.value)} aria-describedby="c-groups-hint" /><p className="hint" id="c-groups-hint">One per line, as group: role. A group with no role gets no access.</p></div>
        <div className="field"><label htmlFor="c-domains">Verified domains</label><textarea id="c-domains" className="textarea mono" rows={4} value={domains} onChange={(e) => setDomains(e.target.value)} aria-describedby="c-domains-hint" /><p className="hint" id="c-domains-hint">One per line. Used for just-in-time membership.</p></div>
      </div>
      <label className="check"><input type="checkbox" checked={jit} onChange={(e) => setJit(e.target.checked)} />Add people from verified domains on their first sign-in</label>
      <label className="check"><input type="checkbox" checked={mfa} onChange={(e) => setMfa(e.target.checked)} />Require multi-factor sign-in for admins</label>
      {conn ? <label className="check"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />Connection is active</label> : null}
      {error ? <Banner kind="error">{error}</Banner> : null}
      <div className="row end">
        <Dialog.Close className="btn quiet" type="button">Cancel</Dialog.Close>
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving" : conn ? "Save connection" : "Create connection"}</button>
      </div>
    </form>
  );
}

function Sso({ s }: { s: Session }) {
  const loaded = useApi<{ connections: Conn[] }>(s.role === "admin" ? "/v1/sso/connections" : null);
  const [editing, setEditing] = useState<Conn | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  if (s.role !== "admin") return <div className="content"><PageHead title="Single sign-on" /><Banner kind="neutral">Only admins manage single sign-on.</Banner></div>;
  const conns = loaded.data?.connections ?? [];
  return (
    <div className="content">
      <PageHead
        title="Single sign-on"
        sub="People sign in through your identity provider. A group must map to a role; no mapping means no access."
        actions={<button type="button" className="btn primary" onClick={() => setEditing("new")}><Plus size={16} aria-hidden="true" />Add connection</button>}
      />
      {notice ? <p className="inline-status block-gap" role="status">{notice}</p> : null}
      {loaded.error ? <LoadError reference={null} retryHref="/settings/sso" /> : !loaded.data ? <PageSkeleton /> : conns.length === 0 ? (
        <EmptyState title="No connections yet" body="Add your identity provider so people can sign in with their work account." icon={<KeyRound size={20} strokeWidth={1.8} />} />
      ) : (
        <ul className="stack" aria-label="Connections">
          {conns.map((c) => (
            <li key={c.id} className="card stack">
              <div className="row between">
                <div>
                  <p className="card-title mono">{cellText(c.slug)}</p>
                  <p className="card-sub">{c.protocol === "oidc" ? "OpenID Connect" : "SAML"}, {cellText(c.issuer)}</p>
                </div>
                <div className="row">
                  <Badge tone={c.status === "active" ? "mint" : "butter"} dot>{humanize(c.status)}</Badge>
                  <button type="button" className="btn sm" onClick={() => setEditing(c)}><Pencil size={14} aria-hidden="true" />Edit</button>
                </div>
              </div>
              <dl className="dl">
                <dt>Groups to roles</dt><dd>{Object.entries(c.group_role_map).map(([g, r]) => `${g}: ${humanize(r)}`).join(", ") || "None"}</dd>
                <dt>Just-in-time</dt><dd>{c.jit_enabled ? `Yes, for ${c.verified_domains.join(", ") || "no domains yet"}` : "No"}</dd>
                <dt>Admin multi-factor</dt><dd>{c.require_admin_mfa ? "Required" : "Not required"}</dd>
              </dl>
            </li>
          ))}
        </ul>
      )}
      <Dialog.Root open={editing !== null} onOpenChange={(v) => { if (!v) setEditing(null); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="sheet" aria-describedby={undefined}>
            <header>
              <Dialog.Title>{editing === "new" ? "Add connection" : "Edit connection"}</Dialog.Title>
              <Dialog.Close className="icon-btn" aria-label="Close"><X size={18} strokeWidth={1.7} /></Dialog.Close>
            </header>
            <div className="body">
              {editing !== null ? (
                <ConnectionForm key={editing === "new" ? "new" : editing.id} conn={editing === "new" ? null : editing}
                  onDone={(text) => { setEditing(null); setNotice(text); loaded.reload(); }} />
              ) : null}
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

export default function SsoPage() { return <SignedIn>{(s) => <Sso s={s} />}</SignedIn>; }
