"use client";
// The sign-in frame for both planes: the CSS 3D stage with a short statement beside the form
// card. The stage is decorative; the copy carries the meaning. Below 1024px the stage shrinks to
// a band above the card (ui/auth.css).
import type { ReactNode } from "react";
import { FileSearch, ShieldCheck, Sparkles, KeyRound, Gauge, Users } from "lucide-react";
import { Stage } from "./Scene";

const COPY = {
  client: {
    title: "Know your accounts before the first call.",
    body: "Research, evidence and changes for every account you work, in one private workspace.",
    points: [
      { icon: FileSearch, text: "Every claim cites its source" },
      { icon: Sparkles, text: "Signals when an account changes" },
      { icon: ShieldCheck, text: "Your data stays in your workspace" },
    ],
  },
  operator: {
    title: "The 6th operator console.",
    body: "Provision clients, set flags and keys, and inspect runs through time-boxed support grants.",
    points: [
      { icon: Users, text: "Clients by id, never by content" },
      { icon: KeyRound, text: "Grants for every lookup" },
      { icon: Gauge, text: "Runs, cost and failures" },
    ],
  },
} as const;

export function AuthFrame({ plane, children }: { plane: "client" | "operator"; children: ReactNode }) {
  const copy = COPY[plane];
  return (
    <main className="auth" data-plane={plane}>
      <Stage plane={plane}>
        <div className="stage-copy">
          <h2>{copy.title}</h2>
          <p>{copy.body}</p>
        </div>
        <ul className="stage-points">
          {copy.points.map(({ icon: Icon, text }) => <li key={text}><Icon size={16} strokeWidth={1.8} aria-hidden="true" />{text}</li>)}
        </ul>
      </Stage>
      <div className="auth-panel">
        <section className="auth-card" aria-labelledby="auth-title">{children}</section>
      </div>
    </main>
  );
}

export function AuthBrand({ plane }: { plane: "client" | "operator" }) {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">6</span>
      <span className="brand-name"><b>{plane === "client" ? "Account Intelligence" : "Operator console"}</b><span>by 6th</span></span>
    </div>
  );
}
