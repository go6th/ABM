"use client";

import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import { formatDate, formatCount } from "@/lib/operator-render";
import { useOperatorViewer } from "../OperatorSessionGate";
import { OperatorRequestState } from "../OperatorStates";
import { useOperatorApi } from "../useOperatorApi";
import { useProviderLabels } from "@/lib/provider-labels";
import { Badge, Banner, PageHead, Section } from "@/ui/primitives";

function messageFrom(error: unknown): string {
  return error instanceof ApiError ? error.detail : "The change could not be completed.";
}

export default function OperatorConfigurationPage() {
  const { providerName, providers: PROVIDERS } = useProviderLabels();
  const viewer = useOperatorViewer();
  const isOperator = viewer.role === "operator";
  const flags = useOperatorApi("GET /operator/v1/flags", {}, isOperator);
  const keys = useOperatorApi("GET /operator/v1/keys", {}, isOperator);
  const [flagBusy, setFlagBusy] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const [flagMessage, setFlagMessage] = useState("");
  const [keyMessage, setKeyMessage] = useState("");
  const isSaved = (text: string) => text.startsWith("Saved");

  async function addFlag(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    let rule: unknown;
    try { rule = JSON.parse(String(fields.get("rule") ?? "")); } catch {
      setFlagMessage("Rule must be valid JSON.");
      return;
    }
    if (typeof rule !== "object" || rule === null || Array.isArray(rule)) {
      setFlagMessage("Rule must be a JSON object.");
      return;
    }
    setFlagBusy(true);
    setFlagMessage("");
    try {
      const result = await operatorRequest("POST /operator/v1/flags", {
        body: { flag: fields.get("flag"), rule, reason_code: fields.get("reason_code") },
      });
      setFlagMessage("Saved " + result.flag + " as version " + result.version + ".");
      form.reset();
      flags.reload();
    } catch (error) { setFlagMessage(messageFrom(error)); }
    finally { setFlagBusy(false); }
  }

  async function addKeyReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setKeyBusy(true);
    setKeyMessage("");
    try {
      const providerCode = String(fields.get("provider_code") ?? "");
      const result = await operatorRequest("POST /operator/v1/keys", {
        body: {
          provider_code: providerCode,
          secret_ref: fields.get("secret_ref"),
          reason_code: fields.get("reason_code"),
        },
      });
      setKeyMessage("Saved " + providerName(result.provider_code) + " (" + result.provider_code + ") reference as version " + result.version + ". The reference value was not returned by the API.");
      form.reset();
      keys.reload();
    } catch (error) { setKeyMessage(messageFrom(error)); }
    finally { setKeyBusy(false); }
  }

  if (!isOperator) {
    return (
      <div>
        <PageHead title="Flags and keys" sub="Read-only support role." />
        <Banner kind="info" title="Restricted to operator staff">Feature flag changes and broker key references require the operator role.</Banner>
      </div>
    );
  }

  return (
    <div>
      <PageHead title="Flags and keys" sub="Versioned configuration changes, each with an audit record." />
      <div className="grid-2">
        <section className="card" aria-labelledby="add-flag-title">
          <h2 id="add-flag-title" className="card-title">Add a feature flag version</h2>
          <p className="card-sub">The API validates the rule and writes a new version. Rules take tenants, plans and a percent.</p>
          <form className="form gap-top" onSubmit={(event) => void addFlag(event)}>
            <div className="field"><label htmlFor="flag-id">Flag identifier</label><input id="flag-id" className="input mono" name="flag" pattern="[a-z_]{1,60}" maxLength={60} placeholder="new_workspace_flow" required /></div>
            <div className="field"><label htmlFor="flag-reason">Reason code</label><input id="flag-reason" className="input mono" name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="approved_change" required /></div>
            <div className="field"><label htmlFor="flag-rule">Rule JSON</label><textarea id="flag-rule" className="textarea mono" name="rule" rows={5} spellCheck={false} placeholder='{"percent": 10}' required /></div>
            <div><button className="btn primary" type="submit" disabled={flagBusy}>{flagBusy ? "Saving" : "Add version"}</button></div>
            {flagMessage ? <p className={isSaved(flagMessage) ? "inline-status" : "inline-error"} role="status">{flagMessage}</p> : null}
          </form>
        </section>
        <section className="card" aria-labelledby="add-key-title">
          <h2 id="add-key-title" className="card-title">Add a broker key reference</h2>
          <p className="card-sub">A secret manager reference path only. The API never returns it. Never paste a provider key here.</p>
          <form className="form gap-top" onSubmit={(event) => void addKeyReference(event)}>
            <div className="field"><label htmlFor="key-provider">Provider</label><select id="key-provider" className="select" name="provider_code" defaultValue="" required><option value="" disabled>Select a provider</option>{PROVIDERS.map((provider) => <option key={provider.code} value={provider.code}>{provider.name} ({provider.code})</option>)}</select></div>
            <div className="field"><label htmlFor="key-ref">Secret manager reference</label><input id="key-ref" className="input mono" name="secret_ref" pattern="[A-Za-z0-9/_.:\-]{1,255}" maxLength={255} autoComplete="off" spellCheck={false} required /></div>
            <div className="field"><label htmlFor="key-reason">Reason code</label><input id="key-reason" className="input mono" name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="key_rotation" required /></div>
            <div><button className="btn primary" type="submit" disabled={keyBusy}>{keyBusy ? "Saving" : "Add reference"}</button></div>
            {keyMessage ? <p className={isSaved(keyMessage) ? "inline-status" : "inline-error"} role="status">{keyMessage}</p> : null}
          </form>
        </section>
      </div>

      <Section title="Current feature flags" id="flags" sub="The latest version of each flag.">
        <OperatorRequestState loading={flags.loading} error={flags.error} empty={Boolean(flags.data && flags.data.flags.length === 0)} onRetry={flags.reload} />
        {flags.data && flags.data.flags.length ? (
          <div className="table-wrap"><table className="table"><caption className="sr-only">Current feature flag rule versions</caption>
            <thead><tr><th scope="col">Flag</th><th scope="col">Version</th><th scope="col">Rule</th></tr></thead>
            <tbody>{flags.data.flags.map((flag) => <tr key={flag.flag}><td><code className="mono">{flag.flag}</code></td><td><Badge tone="peri">{formatCount(flag.version)}</Badge></td><td><pre className="code">{JSON.stringify(flag.rule, null, 2)}</pre></td></tr>)}</tbody>
          </table></div>
        ) : null}
      </Section>
      <Section title="Broker key reference versions" id="keys" action={<Badge>Values are write-only</Badge>}>
        <OperatorRequestState loading={keys.loading} error={keys.error} empty={Boolean(keys.data && keys.data.keys.length === 0)} onRetry={keys.reload} />
        {keys.data && keys.data.keys.length ? (
          <div className="table-wrap"><table className="table"><caption className="sr-only">Key reference versions; secret references are not returned</caption>
            <thead><tr><th scope="col">Provider</th><th scope="col">Version</th><th scope="col">Created by</th><th scope="col">Created</th></tr></thead>
            <tbody>{keys.data.keys.map((key) => <tr key={key.provider_code + ":" + key.version}><td><strong>{providerName(key.provider_code)}</strong> <code className="mono t-footnote ink-2">{key.provider_code}</code></td><td><Badge tone="peri">{formatCount(key.version)}</Badge></td><td><code className="mono t-footnote">{key.created_by}</code></td><td className="nowrap">{formatDate(key.created_at)}</td></tr>)}</tbody>
          </table></div>
        ) : null}
      </Section>
    </div>
  );
}
