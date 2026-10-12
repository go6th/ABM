"use client";
// Tenant settings (TN5) and branding (VW6). Admins edit; everyone else reads. Every save is a new
// version; a stale version is refused by the API and reported here.
import { useState } from "react";
import { api, ApiError, type Session } from "@/lib/api";
import { SignedIn } from "@/ui/SignedIn";
import { Banner, LoadError, PageHead, PageSkeleton, Section } from "@/ui/primitives";
import { humanize } from "@/lib/ws-format";
import { useApi } from "@/lib/useApi";

interface S { platform_lanes: string[]; people_scope: { levels_in_scope: string[]; regions: string[] }; exclusions: { companies: string[]; people: string[] }; pursuing_client: string | null; branding: { display_name: string; accent_hex: string; logo_sha256: string | null } | null }
const LANES = ["salesforce", "servicenow", "sap", "tableau_analytics", "veeva", "data_ai", "other"];
const lines = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);

function Settings({ s }: { s: Session }) {
  const loaded = useApi<{ version: number; settings: S }>("/v1/settings");
  const version = loaded.data?.version ?? 0;
  const [st, setSt] = useState<S | null>(null);
  const [companies, setCompanies] = useState(""); const [people, setPeople] = useState(""); const [regions, setRegions] = useState("");
  const [name, setName] = useState(""); const [accent, setAccent] = useState("#4A57D6");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
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
  if (loaded.error && !loaded.data) return <LoadError reference={null} retryHref="/settings" />;
  if (!st) return <PageSkeleton />;
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api("PUT", "/v1/settings", { expected_version: version, settings: { ...st, people_scope: { ...st.people_scope, regions: lines(regions) }, exclusions: { companies: lines(companies), people: lines(people) } } });
      setMsg({ ok: true, text: "Research preferences saved." }); load();
    } catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? `Research preferences were not saved (${err.type}).` : "Research preferences were not saved." }); }
    finally { setBusy(false); }
  };
  const saveBrand = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^#[0-9a-fA-F]{6}$/.test(accent)) { setMsg({ ok: false, text: "Enter a valid six digit hex accent colour, such as #4A57D6." }); return; }
    setBusy(true);
    setMsg(null);
    try { await api("PUT", "/v1/settings/branding", { expected_version: version, display_name: name, accent_hex: accent, logo_sha256: st.branding?.logo_sha256 ?? null }); setMsg({ ok: true, text: "Branding saved." }); load(); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? `Branding was not saved (${err.type}).` : "Branding was not saved." }); }
    finally { setBusy(false); }
  };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type)) { setMsg({ ok: false, text: "Choose a PNG or JPEG image." }); return; }
    setBusy(true);
    setMsg(null);
    try {
      const logo = await api<{ logo_sha256: string }>("POST", "/v1/settings/branding/logo", file, { "Content-Type": "application/octet-stream" });
      await api("PUT", "/v1/settings/branding", { expected_version: version, display_name: name || "Brand", accent_hex: accent, logo_sha256: logo.logo_sha256 });
      setMsg({ ok: true, text: "Logo saved." }); load();
    } catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? `Logo was not saved (${err.type}).` : "Logo was not saved." }); }
    finally { setBusy(false); }
  };
  return (
    <div className="content">
      <PageHead title="Workspace settings" sub="Research defaults and branding for this workspace. Every save is a new version." />
      {!admin ? <Banner kind="neutral">Only admins change these settings. You can read them.</Banner> : null}
      {msg ? <p className={msg.ok ? "inline-status block-gap" : "inline-error block-gap"} role="status">{msg.text}</p> : null}
      <Section title="Research" sub="What a refresh covers and what it leaves out.">
        <form className="card" onSubmit={(e) => void save(e)}>
          <fieldset className="form" disabled={!admin || busy}>
            <div className="field">
              <span className="label">Platform lanes</span>
              <div className="grid-2">
                {LANES.map((l) => (
                  <label key={l} className="check"><input type="checkbox" checked={st.platform_lanes.includes(l)}
                    onChange={(e) => setSt({ ...st, platform_lanes: e.target.checked ? [...st.platform_lanes, l] : st.platform_lanes.filter((x) => x !== l) })} />{humanize(l)}</label>
                ))}
              </div>
            </div>
            <div className="field">
              <label htmlFor="regions">People regions</label>
              <textarea id="regions" className="textarea" rows={3} value={regions} onChange={(e) => setRegions(e.target.value)} aria-describedby="regions-hint" />
              <p className="hint" id="regions-hint">One per line. Leave empty to cover every region.</p>
            </div>
            <div className="field">
              <label htmlFor="client">Pursuing client</label>
              <input id="client" className="input" value={st.pursuing_client ?? ""} maxLength={120} onChange={(e) => setSt({ ...st, pursuing_client: e.target.value || null })} />
            </div>
            <div className="grid-2">
              <div className="field">
                <label htmlFor="xc">Excluded companies</label>
                <textarea id="xc" className="textarea" rows={4} value={companies} onChange={(e) => setCompanies(e.target.value)} aria-describedby="xc-hint" />
                <p className="hint" id="xc-hint">Web domains, one per line.</p>
              </div>
              <div className="field">
                <label htmlFor="xp">Excluded people</label>
                <textarea id="xp" className="textarea" rows={4} value={people} onChange={(e) => setPeople(e.target.value)} aria-describedby="xp-hint" />
                <p className="hint" id="xp-hint">Profile links or names, one per line.</p>
              </div>
            </div>
            <div><button className="btn primary" type="submit" disabled={!admin || busy}>{busy ? "Saving" : "Save research preferences"}</button></div>
          </fieldset>
        </form>
      </Section>
      <Section title="Branding" sub="The name, colour and logo in views and document headers. The attribution stays 6th agents.">
        <form className="card" onSubmit={(e) => void saveBrand(e)}>
          <fieldset className="form" disabled={!admin || busy}>
            <div className="grid-2">
              <div className="field"><label htmlFor="bn">Display name</label><input id="bn" className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} /></div>
              <div className="field"><label htmlFor="ba">Accent colour</label><input id="ba" className="input mono" value={accent} maxLength={7} onChange={(e) => setAccent(e.target.value)} /></div>
            </div>
            <div className="field">
              <label htmlFor="bl">Logo</label>
              <input id="bl" className="input" type="file" accept="image/png,image/jpeg" onChange={(e) => void upload(e.target.files?.[0])} aria-describedby="bl-hint" />
              <p className="hint" id="bl-hint">PNG or JPEG.</p>
            </div>
            <div><button className="btn primary" type="submit" disabled={!admin || busy}>{busy ? "Saving" : "Save branding"}</button></div>
          </fieldset>
        </form>
      </Section>
    </div>
  );
}

export default function SettingsPage() { return <SignedIn>{(s) => <Settings s={s} />}</SignedIn>; }
