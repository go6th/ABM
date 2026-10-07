"use client";
// Sign in through the organisation's own identity provider. The browser is sent to the API's
// sign-in start for the connection; the tenant comes from that connection, never from here.
import { useState } from "react";

const SLUG = /^[a-z0-9][a-z0-9-]{2,62}$/;

export default function Login() {
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const go = (e: React.FormEvent) => {
    e.preventDefault();
    const s = slug.trim().toLowerCase();
    if (!SLUG.test(s)) { setError("Enter your organisation's sign-in name."); return; }
    const raw = new URLSearchParams(window.location.search).get("return_to") ?? "/";
    const returnTo = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";
    // the sign-in start is an API route (a redirect to the IdP), not a page of this app
    window.location.href = new URL(`/v1/sso/${s}/start?return_to=${encodeURIComponent(returnTo)}`, window.location.origin).toString();
  };
  return (
    <section>
      <h1>Sign in</h1>
      <form onSubmit={go} className="card">
        <label htmlFor="org">Organisation sign-in name</label>
        <div className="row">
          <input id="org" name="org" autoComplete="organization" value={slug} onChange={(e) => setSlug(e.target.value)} />
          <button className="primary" type="submit">Continue</button>
        </div>
        {error ? <p className="error" role="alert">{error}</p> : null}
      </form>
    </section>
  );
}
