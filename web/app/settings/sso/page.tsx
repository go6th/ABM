"use client";
// The tenant's identity provider connections (admins). The client secret is write-only.
import { useEffect, useState } from "react";
import { api, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { cellText } from "@/lib/render";

interface Conn { id: string; slug: string; protocol: string; issuer: string; status: string; jit_enabled: boolean; has_client_secret: boolean; verified_domains: string[]; group_role_map: Record<string, string> }

function Sso({ s }: { s: Session }) {
  const [conns, setConns] = useState<Conn[]>([]);
  useEffect(() => { if (s.role === "admin") void api<{ connections: Conn[] }>("GET", "/v1/sso/connections").then((b) => setConns(b.connections)); }, [s.role]);
  if (s.role !== "admin") return <p>Only admins manage single sign-on.</p>;
  return (
    <section>
      <h1>Single sign-on</h1>
      <p className="muted">People sign in only through your identity provider. A group must map to a role; no mapping means no access.</p>
      <table className="data"><thead><tr><th scope="col">Sign-in name</th><th scope="col">Protocol</th><th scope="col">Issuer</th><th scope="col">Groups to roles</th><th scope="col">Just-in-time</th><th scope="col">Status</th></tr></thead>
        <tbody>{conns.map((c) => (
          <tr key={c.id}><td>{cellText(c.slug)}</td><td>{c.protocol}</td><td>{cellText(c.issuer)}</td>
            <td>{Object.entries(c.group_role_map).map(([g, r]) => `${g}: ${r}`).join(", ")}</td>
            <td>{c.jit_enabled ? `Yes (${c.verified_domains.join(", ")})` : "No"}</td><td>{c.status}</td></tr>
        ))}</tbody></table>
    </section>
  );
}

export default function SsoPage() { return <SignedIn>{(s) => <Sso s={s} />}</SignedIn>; }
