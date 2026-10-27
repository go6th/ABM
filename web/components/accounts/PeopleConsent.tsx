"use client";
// People data consent: the workspace grants or withdraws the consent that lets a research run
// build the senior people list from public professional information. Without it the people
// rows of every run end "needs your consent for people data". An admin grants or withdraws it;
// everyone can read where it stands. The text and its version come from the server.
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { PeopleConsent as Consent } from "@/lib/gen/api.gen";
import { Pill } from "@/ui/design";

function failure(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 403) return "Only an administrator can change this, after a recent multi-factor sign-in.";
    if (cause.status === 409) return "There is no approved consent text to accept yet.";
  }
  return "The change could not be saved. Try again.";
}

export function PeopleConsentCard({ consent, canChange }: { consent: Consent; canChange: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = consent.granted && consent.current;

  const set = async (accept: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api<Consent>("POST", "/v1/settings/people-consent", { accept });
      router.refresh();
    } catch (cause) {
      setError(failure(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-labelledby="people-consent-title">
      <div className="row between">
        <h2 id="people-consent-title">{consent.label}</h2>
        <Pill tone={active ? "ok" : "neutral"}>{active ? "Granted" : consent.granted ? "Needs renewing" : "Not granted"}</Pill>
      </div>
      <p className="muted">{consent.text}</p>
      <p className="muted small">
        Version {consent.text_version}.
        {consent.granted_at && active ? ` Granted ${new Date(consent.granted_at).toLocaleDateString()}.` : ""}
        {consent.granted && !consent.current ? " The text changed since it was granted; grant it again to resume people research." : ""}
        {!consent.granted ? " Until it is granted, research runs skip the people list and say so." : ""}
      </p>
      {canChange ? (
        <div className="row gap">
          {active ? (
            <button type="button" className="btn" disabled={busy} onClick={() => set(false)}>Withdraw consent</button>
          ) : (
            <button type="button" className="btn primary" disabled={busy} onClick={() => set(true)}>Grant consent</button>
          )}
        </div>
      ) : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
    </section>
  );
}
