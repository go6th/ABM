"use client";

import { useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { operatorRequest } from "@/lib/operator-api";
import { formatDate, formatCount } from "@/lib/operator-render";
import { useOperatorViewer } from "../OperatorSessionGate";
import { OperatorRequestState } from "../OperatorStates";
import { useOperatorApi } from "../useOperatorApi";
import { providerName, PROVIDERS } from "@/lib/provider-labels";

function messageFrom(error: unknown): string {
  return error instanceof ApiError ? error.detail : "The change could not be completed.";
}

export default function OperatorConfigurationPage() {
  const viewer = useOperatorViewer();
  const isOperator = viewer.role === "operator";
  const flags = useOperatorApi("GET /operator/v1/flags", {}, isOperator);
  const keys = useOperatorApi("GET /operator/v1/keys", {}, isOperator);
  const [flagBusy, setFlagBusy] = useState(false);
  const [keyBusy, setKeyBusy] = useState(false);
  const [flagMessage, setFlagMessage] = useState("");
  const [keyMessage, setKeyMessage] = useState("");

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

  if (!isOperator) return <section><div className="operator-page-heading"><div><p className="eyebrow">PLATFORM OPERATIONS</p><h1>Flags & keys</h1><p>Read-only support role.</p></div></div><div className="banner tone-sky"><span aria-hidden="true">ⓘ</span><div className="banner-body"><strong>Restricted to operator staff</strong><p>Feature flag changes and broker key references require the operator role.</p></div></div></section>;

  return <section>
    <div className="operator-page-heading"><div><p className="eyebrow">PLATFORM OPERATIONS</p><h1>Flags & keys</h1><p>Versioned configuration changes with backend audit records.</p></div></div>
    <div className="operator-grid">
      <section className="operator-card">
        <h2>Add feature flag version</h2>
        <p className="muted">The API validates the rule and writes a new version. The form accepts a JSON object.</p>
        <form className="operator-admin-form" onSubmit={(event) => void addFlag(event)}>
          <label className="field"><span>Flag identifier</span><input name="flag" pattern="[a-z_]{1,60}" maxLength={60} placeholder="new_workspace_flow" required /></label>
          <label className="field"><span>Reason code</span><input name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="approved_change" required /></label>
          <label className="field"><span>Rule JSON</span><textarea name="rule" rows={5} spellCheck={false} placeholder='{"enabled": true}' required /></label>
          <button className="btn primary" type="submit" disabled={flagBusy}>{flagBusy ? "Saving…" : "Add version"}</button>
          {flagMessage ? <p className="operator-action-message" role="status">{flagMessage}</p> : null}
        </form>
      </section>
      <section className="operator-card">
        <h2>Add broker key reference</h2>
        <p className="muted">Enter a secret-manager reference path only. The API never returns this value; do not paste a provider key here.</p>
        <form className="operator-admin-form" onSubmit={(event) => void addKeyReference(event)}>
          <label className="field"><span>Provider</span><select className="select" name="provider_code" defaultValue="" required><option value="" disabled>Select a provider</option>{PROVIDERS.map((provider) => <option key={provider.code} value={provider.code}>{provider.name} ({provider.code})</option>)}</select></label>
          <label className="field"><span>Secret manager reference</span><input name="secret_ref" pattern="[A-Za-z0-9/_.:-]{1,255}" maxLength={255} autoComplete="off" spellCheck={false} required /></label>
          <label className="field"><span>Reason code</span><input name="reason_code" pattern="[a-z_]{1,40}" maxLength={40} placeholder="key_rotation" required /></label>
          <button className="btn primary" type="submit" disabled={keyBusy}>{keyBusy ? "Saving…" : "Add reference"}</button>
          {keyMessage ? <p className="operator-action-message" role="status">{keyMessage}</p> : null}
        </form>
      </section>
    </div>

    <section className="operator-section">
      <div className="operator-section-title"><h2>Current feature flags</h2><span className="muted">Latest server versions</span></div>
      <OperatorRequestState loading={flags.loading} error={flags.error} empty={Boolean(flags.data && flags.data.flags.length === 0)} onRetry={flags.reload} />
      {flags.data && flags.data.flags.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Current feature flag rule versions</caption><thead><tr><th scope="col">Flag</th><th scope="col">Version</th><th scope="col">Rule</th></tr></thead><tbody>{flags.data.flags.map((flag) => <tr key={flag.flag}><td><code>{flag.flag}</code></td><td>v{formatCount(flag.version)}</td><td><pre className="operator-code-block">{JSON.stringify(flag.rule, null, 2)}</pre></td></tr>)}</tbody></table></div> : null}
    </section>
    <section className="operator-section">
      <div className="operator-section-title"><h2>Broker key reference versions</h2><span className="badge tone-slate">Write-only values</span></div>
      <OperatorRequestState loading={keys.loading} error={keys.error} empty={Boolean(keys.data && keys.data.keys.length === 0)} onRetry={keys.reload} />
      {keys.data && keys.data.keys.length ? <div className="operator-table-scroll"><table className="operator-table"><caption className="sr-only">Key reference versions; secret references are not returned</caption><thead><tr><th scope="col">Provider</th><th scope="col">Version</th><th scope="col">Created by</th><th scope="col">Created at</th></tr></thead><tbody>{keys.data.keys.map((key) => <tr key={key.provider_code + ":" + key.version}><td><strong>{providerName(key.provider_code)}</strong> <code>{key.provider_code}</code></td><td>v{formatCount(key.version)}</td><td><code>{key.created_by}</code></td><td>{formatDate(key.created_at)}</td></tr>)}</tbody></table></div> : null}
    </section>
  </section>;
}
