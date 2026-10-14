"use client";
// The two default models, saved to the user's own profile (PUT /v1/me/model-preferences).
import { useState } from "react";
import { ModelMenu, useModelChoices } from "@/components/ModelMenu";
import { api, ApiError } from "@/lib/api";
import type { ModelChoices } from "@/lib/gen/api.gen";
import { StateCard } from "@/ui/design";

const ROLES = [
  { key: "research", role: "Research", text: "Pulls and reads public sources and extracts facts." },
  { key: "analysis", role: "Analysis", text: "Writes answers, analyses your files and writes the deliverables." },
] as const;

export function ModelPreferences() {
  const loaded = useModelChoices();
  const [saved, setSaved] = useState<ModelChoices | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const choices = saved ?? loaded;
  if (!choices) return <div className="card pad"><div className="skel line" /><div className="skel line w80" /></div>;
  if (!choices.models.length) return <StateCard title="No models are available" body="Ask your administrator to set up a model." />;

  const save = async (role: "research" | "analysis", id: string) => {
    setMessage(null);
    try {
      const next = await api<ModelChoices>("PUT", "/v1/me/model-preferences", { [`${role}_model`]: id });
      setSaved(next);
      setMessage({ ok: true, text: "Saved." });
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof ApiError ? cause.detail : "Not saved. Try again." });
    }
  };

  return (
    <section className="card pad stack-14" aria-label="Default models">
      {ROLES.map((r) => (
        <div key={r.key} className="row-16 wrap line-top">
          <div className="grow-240"><b>{r.role} model</b><div className="mut sm-text">{r.text}</div></div>
          <ModelMenu choices={choices} value={choices[r.key]} onChange={(id) => void save(r.key, id)} role={r.role} menuDown className="btn sm" />
        </div>
      ))}
      {message ? <p className={`sm-text ${message.ok ? "mut" : "bad-text"}`} role={message.ok ? "status" : "alert"}>{message.text}</p> : null}
    </section>
  );
}
