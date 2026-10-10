"use client";
// Tenant settings (TN5) and branding (VW6). Admins edit; everyone else reads. Every save is a new
// version; a stale version is refused by the API and reported here.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/components/SignedIn";
import { useApi } from "@/lib/useApi";

interface S { platform_lanes: string[]; people_scope: { levels_in_scope: string[]; regions: string[] }; exclusions: { companies: string[]; people: string[] }; pursuing_client: string | null; branding: { display_name: string; accent_hex: string; logo_sha256: string | null } | null }
const LANES = ["salesforce", "servicenow", "sap", "tableau_analytics", "veeva", "data_ai", "other"];
const lines = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);

function Settings({ s }: { s: Session }) {
  const loaded = useApi<{ version: number; settings: S }>("/v1/settings");
  const version = loaded.data?.version ?? 0;
  const [st, setSt] = useState<S | null>(null);
  const [companies, setCompanies] = useState(""); const [people, setPeople] = useState(""); const [regions, setRegions] = useState("");
  const [name, setName] = useState(""); const [accent, setAccent] = useState("#1f4e79");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const admin = s.role === "admin";
  const [seen, setSeen] = useState<number | null>(null);
  const fresh = loaded.data;
  if (fresh && seen !== fresh.version) {
    // copy the loaded version into the form once per version (React's derive-from-props pattern)
    setSeen(fresh.version); setSt(fresh.settings);
    setCompanies(fresh.settings.exclusions.companies.join("\n")); setPeople(fresh.settings.exclusions.people.join("\n"));
    setRegions(fresh.settings.people_scope.regions.join("\n"));
    if (fresh.settings.branding) { setName(fresh.settings.branding.display_name); setAccent(fresh.settings.branding.accent_hex); }
  }
  const load = loaded.reload;
  if (loaded.error && !loaded.data) return <div className="client-data-state error-box" role="alert"><strong>Settings could not be loaded</strong><p>{loaded.error.detail}</p><button className="btn" type="button" onClick={load}>Try again</button></div>;
  if (!st) return <p className="client-loading" role="status">Loading workspace settings…</p>;
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api("PUT", "/v1/settings", { expected_version: version, settings: { ...st, people_scope: { ...st.people_scope, regions: lines(regions) }, exclusions: { companies: lines(companies), people: lines(people) } } });
      setMsg("Research preferences saved."); load();
    } catch (err) { setMsg(err instanceof ApiError ? `Research preferences were not saved (${err.type}).` : "Research preferences were not saved."); }
    finally { setBusy(false); }
  };
  const saveBrand = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^#[0-9a-fA-F]{6}$/.test(accent)) { setMsg("Enter a valid six-digit hex accent colour."); return; }
    setBusy(true);
    setMsg(null);
    try { await api("PUT", "/v1/settings/branding", { expected_version: version, display_name: name, accent_hex: accent, logo_sha256: st.branding?.logo_sha256 ?? null }); setMsg("Branding saved."); load(); }
    catch (err) { setMsg(err instanceof ApiError ? `Branding was not saved (${err.type}).` : "Branding was not saved."); }
    finally { setBusy(false); }
  };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type)) { setMsg("Choose a PNG or JPEG image."); return; }
    setBusy(true);
    setMsg(null);
    try {
      const logo = await api<{ logo_sha256: string }>("POST", "/v1/settings/branding/logo", file, { "Content-Type": "application/octet-stream" });
      await api("PUT", "/v1/settings/branding", { expected_version: version, display_name: name || "Brand", accent_hex: accent, logo_sha256: logo.logo_sha256 });
      setMsg("Logo saved."); load();
    } catch (err) { setMsg(err instanceof ApiError ? `Logo was not saved (${err.type}).` : "Logo was not saved."); }
    finally { setBusy(false); }
  };
  return (
    <section className="client-dashboard">
      <header className="client-dashboard-heading"><div><p className="eyebrow">WORKSPACE ADMINISTRATION</p><h1>Organisation settings</h1>
        <p className="secondary">Configure tenant research defaults and branding.</p></div></header>
      {msg ? <p role="status">{msg}</p> : null}
      <form className="card" onSubmit={save}>
        <h2>Research</h2>
        <fieldset disabled={!admin || busy}><legend>Platform lanes</legend>
          {LANES.map((l) => (
            <label key={l} className="row"><input type="checkbox" checked={st.platform_lanes.includes(l)}
              onChange={(e) => setSt({ ...st, platform_lanes: e.target.checked ? [...st.platform_lanes, l] : st.platform_lanes.filter((x) => x !== l) })} />{l.replaceAll("_", " ")}</label>
          ))}
          <label htmlFor="regions">People regions (one per line; empty is global)</label>
          <textarea id="regions" rows={3} value={regions} onChange={(e) => setRegions(e.target.value)} />
          <label htmlFor="client">Pursuing client</label>
          <input id="client" value={st.pursuing_client ?? ""} maxLength={120} onChange={(e) => setSt({ ...st, pursuing_client: e.target.value || null })} />
          <label htmlFor="xc">Excluded companies (domains, one per line)</label>
          <textarea id="xc" rows={4} value={companies} onChange={(e) => setCompanies(e.target.value)} />
          <label htmlFor="xp">Excluded people (profile links or names, one per line)</label>
          <textarea id="xp" rows={4} value={people} onChange={(e) => setPeople(e.target.value)} />
          <div><button className="btn primary" type="submit" disabled={!admin || busy}>{busy ? "Saving…" : "Save research preferences"}</button></div>
        </fieldset>
      </form>
      <form className="card" onSubmit={saveBrand}>
        <h2>Branding</h2>
        <p className="muted">Branding changes the name, colour and logo in views and document headers. The attribution stays &quot;6th agents&quot;.</p>
        <fieldset disabled={!admin || busy}>
          <label htmlFor="bn">Display name</label><input id="bn" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
          <label htmlFor="ba">Accent colour</label><input id="ba" value={accent} maxLength={7} onChange={(e) => setAccent(e.target.value)} />
          <label htmlFor="bl">Logo (PNG or JPEG)</label><input id="bl" type="file" accept="image/png,image/jpeg" onChange={(e) => void upload(e.target.files?.[0])} />
          <div><button className="btn primary" type="submit" disabled={!admin || busy}>{busy ? "Saving…" : "Save branding"}</button></div>
        </fieldset>
      </form>
    </section>
  );
}

export default function SettingsPage() { return <SignedIn>{(s) => <Settings s={s} />}</SignedIn>; }
