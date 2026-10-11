"use client";
// The workspace's own model key (bring your own key): status, save encrypted, remove. Admins
// only; the server refuses everyone else. Moved here from the old chat page so Conversations
// stays a reading screen. The key value is never returned by the API or shown again.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface ProviderKeyStatus { available: boolean; configured: boolean; updated_at: string | null }

export function ProviderKeyManager() {
  const [status, setStatus] = useState<ProviderKeyStatus | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    let current = true;
    api<ProviderKeyStatus>("GET", "/v1/chat/provider-key")
      .then((value) => { if (current) setStatus(value); })
      .catch(() => { if (current) setFeedback("Key settings could not be loaded."); });
    return () => { current = false; };
  }, []);

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!keyValue || saving) return;
    setSaving(true);
    setFeedback("");
    try {
      setStatus(await api<ProviderKeyStatus>("PUT", "/v1/chat/provider-key", { api_key: keyValue }));
      setKeyValue("");
      setFeedback("Your key was encrypted and saved.");
    } catch {
      setFeedback("The key could not be saved. Check it and try again.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    setFeedback("");
    try {
      await api("DELETE", "/v1/chat/provider-key");
      setStatus((prev) => (prev ? { ...prev, configured: false, updated_at: null } : prev));
      setFeedback("Your saved key was removed.");
    } catch {
      setFeedback("The saved key could not be removed.");
    } finally {
      setSaving(false);
    }
  };

  if (status === null) return <p role="status">{feedback || "Loading key settings"}</p>;
  if (!status.available) return <p>This environment does not enable encrypted tenant keys.</p>;
  return (
    <div className="ws-key">
      <p>{status.configured ? "A workspace key is active for new conversations." : "The default key is active."}</p>
      <form className="line" onSubmit={(event) => void save(event)}>
        <label className="ws-label" htmlFor="tenant-provider-key">Anthropic API key</label>
        <input
          id="tenant-provider-key" className="ws-input" type="password" autoComplete="new-password" spellCheck={false}
          value={keyValue} onChange={(event) => setKeyValue(event.target.value)} maxLength={512} disabled={saving}
        />
        <button className="ws-btn primary" type="submit" disabled={saving || !keyValue}>Save encrypted key</button>
      </form>
      {status.configured ? <button className="ws-btn quiet" type="button" onClick={() => void remove()} disabled={saving}>Remove saved key</button> : null}
      {feedback ? <p role="status">{feedback}</p> : null}
    </div>
  );
}
