"use client";
// Clients (A2): every client with users, accounts, this month's credits (amber near the limit),
// last activity and status; each row opens Client detail. Add client asks for the name and the
// first user's email and shows an invite link to copy; nothing is sent automatically.
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import type { ClientInvite } from "@/lib/gen/api.gen";
import { useOperatorApi } from "../useOperatorApi";
import { ConsoleLoad } from "../ConsoleLoad";
import { Bar, Pill } from "@/ui/design";
import { clientTone, lastActive, num } from "@/lib/console";

function AddClient({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<ClientInvite | null>(null);
  const [copied, setCopied] = useState(false);

  const reset = (next: boolean) => {
    setOpen(next);
    if (!next) { setName(""); setEmail(""); setError(null); setInvite(null); setCopied(false); }
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await operatorRequest("POST /operator/v1/console/clients", { body: { name: name.trim(), admin_email: email.trim() } });
      setInvite(made);
      onAdded();
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 422 ? "Enter a client name and a valid email address." : cause instanceof ApiError && cause.detail ? cause.detail : "The client could not be added. Try again.");
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    if (!invite) return;
    try { await navigator.clipboard.writeText(invite.invite_url); setCopied(true); } catch { setCopied(false); }
  };

  return (
    <Dialog.Root open={open} onOpenChange={reset}>
      <Dialog.Trigger className="btn primary">Add client</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>{invite ? "Client added" : "Add client"}</Dialog.Title>
          {invite ? (
            <>
              <Dialog.Description>{invite.emailed
                ? `The invite was emailed to ${invite.email}. You can also copy the link below and send it yourself.`
                : `Copy this invite link and send it to ${invite.email}. No email was sent.`} The link works once and expires in 24 hours; you can make a new one from the client&apos;s page.</Dialog.Description>
              <div className="invite-link">{invite.invite_url}</div>
              <div className="row-12 wrap">
                <button type="button" className="btn primary" onClick={() => void copy()}>{copied ? "Copied" : "Copy invite link"}</button>
                <Link className="btn" href={`/operator/clients/${invite.tenant_id}`}>Open client</Link>
              </div>
            </>
          ) : (
            <form className="stack-14" onSubmit={(e) => void submit(e)}>
              <Dialog.Description>The client gets its own sealed workspace with the platform defaults. Their first user joins as a member; admin rights come through the client&apos;s own sign-in provider with multi-factor sign-in.</Dialog.Description>
              <div className="field"><label htmlFor="client-name">Client name</label><input id="client-name" className="input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></div>
              <div className="field"><label htmlFor="client-email">First user&apos;s email</label><input id="client-email" className="input" type="email" required maxLength={320} value={email} onChange={(e) => setEmail(e.target.value)} /></div>
              {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
              <div className="row-12 wrap">
                <button type="submit" className="btn primary" disabled={busy}>{busy ? "Adding" : "Add client"}</button>
                <Dialog.Close className="btn">Cancel</Dialog.Close>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default function Clients() {
  const data = useOperatorApi("GET /operator/v1/console/clients");
  if (!data.data) return <ConsoleLoad title="Clients" state={data} />;
  const rows = data.data.clients;
  return (
    <div className="pg admin">
      <div className="head">
        <div className="ttl"><h1>Clients</h1><div className="sub">{rows.length} client{rows.length === 1 ? "" : "s"}. Each client is a sealed workspace.</div></div>
        <AddClient onAdded={data.reload} />
      </div>
      <div className="card tscroll">
        <div className="trow th cols-clients">
          <span>Client</span><span>Users</span><span>Accounts</span><span>Credits this month</span><span>Last active</span><span>Status</span><span />
        </div>
        {rows.map((c) => {
          const used = c.credits_used + c.credits_held;
          const near = c.credit_pool > 0 && used >= 0.85 * c.credit_pool;
          return (
            <Link key={c.tenant_id} className="trow cols-clients" href={`/operator/clients/${c.tenant_id}`}>
              <b>{c.name}</b>
              <span>{num(c.users)}</span>
              <span>{num(c.accounts)}</span>
              <span>
                {num(used)} of {num(c.credit_pool)}
                <Bar value={used} max={Math.max(1, c.credit_pool)} tone={near ? "warn" : undefined} label={`${num(used)} of ${num(c.credit_pool)} credits`} />
              </span>
              <span>{lastActive(c.last_active_at)}</span>
              <span><Pill tone={clientTone(c.status)} small>{c.status}</Pill></span>
              <ChevronRight className="chev" size={16} aria-hidden="true" />
            </Link>
          );
        })}
        {!rows.length ? <div className="state"><h2>No clients yet</h2><p>Add the first client to give them a workspace.</p></div> : null}
      </div>
      <p className="mut xs-text">Client names are shown only here, in the admin console. Clients never see each other.</p>
    </div>
  );
}
