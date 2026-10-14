"use client";
// 6th Library (A5): upload a finished account workspace (zip) or a standing file; the platform
// finds the five deliverables, leaves the internal-only file out and runs the client-ready check
// (the pre-delivery gate's checks). Each account shows what was found, the check and how many
// clients have it; failing accounts list their issues. Publish sends every passed account to the
// clients chosen here (or per client on Client detail).
import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import { useOperatorApi } from "../useOperatorApi";
import { ConsoleLoad } from "../ConsoleLoad";
import { Pill } from "@/ui/design";

const DOMAIN = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

function Upload({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const site = domain.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file || !DOMAIN.test(site)) return;
    setBusy(true);
    setMessage(null);
    try {
      const done = await operatorRequest("POST /operator/v1/library/uploads", {
        query: { filename: file.name, domain: site, account_name: name.trim() || undefined },
        file,
      });
      setMessage(done.check_passed
        ? { ok: true, text: `${done.name}: ${done.deliverables_found} of 5 deliverables found. Client-ready check passed.` }
        : { ok: false, text: `${done.name}: ${done.issues.length} issue${done.issues.length === 1 ? "" : "s"} to fix before publishing.` });
      setFile(null);
      setName("");
      setDomain("");
      onDone();
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof ApiError && cause.status === 413 ? "Upload a file up to 100 MB." : cause instanceof ApiError && cause.status === 422 ? "That file could not be read as an account workspace." : "The upload did not finish. Try again." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card pad stack-14" onSubmit={(e) => void submit(e)} aria-labelledby="upload-title">
      <div>
        <h2 id="upload-title" className="card-title">Drop account workspace zips or standing files here</h2>
        <p className="mut sm-text">We recognize the five deliverables per account, leave out internal documents, and check what is client ready.</p>
      </div>
      <div className="row-12 wrap align-top">
        <div className="field grow-240"><label htmlFor="lib-file">File</label><input id="lib-file" className="input" type="file" accept=".zip,.docx,.xlsx" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
        <div className="field grow-220"><label htmlFor="lib-name">Account name</label><input id="lib-name" className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="Optional" /></div>
        <div className="field grow-220"><label htmlFor="lib-domain">Account website</label><input id="lib-domain" className="input" required value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" aria-invalid={domain !== "" && !DOMAIN.test(site)} /></div>
      </div>
      {message ? <p className={`sm-text ${message.ok ? "mut" : "bad-text"}`} role={message.ok ? "status" : "alert"}>{message.text}</p> : null}
      <button type="submit" className="btn primary align-start" disabled={busy || !file || !DOMAIN.test(site)}>{busy ? "Checking" : "Upload and check"}</button>
    </form>
  );
}

function PublishPassed({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const clients = useOperatorApi("GET /operator/v1/console/clients");
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const publish = async () => {
    setBusy(true);
    setError(null);
    try {
      for (const id of ids) {
        await operatorRequest("POST /operator/v1/library/{library_account_id}/publish", { params: { library_account_id: id }, body: { tenant_ids: [...chosen] } });
      }
      setOpen(false);
      setChosen(new Set());
      onDone();
    } catch {
      setError("Publishing did not finish. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className="btn primary" disabled={!ids.length}>Publish {ids.length} passed account{ids.length === 1 ? "" : "s"}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title>Publish to clients</Dialog.Title>
          <Dialog.Description>Each client sees only the accounts you assign to it.</Dialog.Description>
          <fieldset className="stack-6">
            <legend className="sr-only">Clients</legend>
            {(clients.data?.clients ?? []).map((c) => (
              <label key={c.tenant_id} className="check">
                <input type="checkbox" checked={chosen.has(c.tenant_id)} onChange={(e) => setChosen((prev) => { const next = new Set(prev); if (e.target.checked) next.add(c.tenant_id); else next.delete(c.tenant_id); return next; })} />
                {c.name}
              </label>
            ))}
          </fieldset>
          {error ? <p className="sm-text bad-text" role="alert">{error}</p> : null}
          <div className="row-12 wrap">
            <button type="button" className="btn primary" disabled={busy || !chosen.size} onClick={() => void publish()}>{busy ? "Publishing" : `Publish to ${chosen.size} client${chosen.size === 1 ? "" : "s"}`}</button>
            <Dialog.Close className="btn">Cancel</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default function Library() {
  const data = useOperatorApi("GET /operator/v1/library");
  if (!data.data) return <ConsoleLoad title="6th Library" state={data} />;
  const rows = data.data.accounts;
  const failing = rows.filter((a) => !a.check_passed);
  return (
    <div className="pg admin">
      <div className="head"><div className="ttl"><h1>6th Library</h1><div className="sub">Seed content from research you have already done. Publish it to the clients you choose.</div></div></div>
      <Upload onDone={data.reload} />
      <div className="card tscroll" tabIndex={0} role="region" aria-label="Library accounts">
        <div className="trow th cols-library"><span>Account</span><span>Deliverables found</span><span>Internal-only file</span><span>Client-ready check</span><span>Publish to</span></div>
        {rows.map((a) => (
          <div key={a.id} className={a.check_passed ? "trow cols-library" : "trow cols-library flag"}>
            <b>{a.name}<span className="sub">{a.domain}</span></b>
            <span>{a.deliverables_found} of 5</span>
            <span>{a.internal_left_out ? "Left out" : "None found"}</span>
            <span>{a.check_passed ? <Pill tone="ok" small>Passed</Pill> : <Pill tone="warn" small>{a.issues.length} issue{a.issues.length === 1 ? "" : "s"}</Pill>}</span>
            <span>{a.clients ? `${a.clients} client${a.clients === 1 ? "" : "s"}` : "Not published"}</span>
          </div>
        ))}
        {!rows.length ? <div className="state"><h2>The library is empty</h2><p>Upload a finished account workspace to start.</p></div> : null}
      </div>
      {failing.map((a) => (
        <div key={a.id} className="notice warn">
          <b>{a.name}: {a.issues.length} issue{a.issues.length === 1 ? "" : "s"} to fix before publishing</b>
          <p>{a.issues.join(" ")} Fix the source files and upload again.</p>
        </div>
      ))}
      <div className="row-12 wrap">
        <PublishPassed ids={rows.filter((a) => a.check_passed).map((a) => a.id)} onDone={data.reload} />
        <span className="mut sm-text">Each client sees only the accounts you assign to it.</span>
      </div>
    </div>
  );
}
