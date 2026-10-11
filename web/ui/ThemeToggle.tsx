"use client";
// Light, Dark, System. Writes the ai_theme cookie and swaps data-theme in place; the change
// animates colour only, for 250ms (html.theme-switching in ui/base.css).
import { useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { m } from "@/lib/messages";
import { type ThemeChoice, themeAttribute, themeCookie } from "./theme";

const OPTIONS: Array<{ value: ThemeChoice; label: string; Icon: typeof Sun }> = [
  { value: "light", label: m.theme.light, Icon: Sun },
  { value: "dark", label: m.theme.dark, Icon: Moon },
  { value: "system", label: m.theme.system, Icon: Monitor },
];

export function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduce) root.classList.add("theme-switching");
  const attribute = themeAttribute(choice);
  if (attribute) root.dataset.theme = attribute;
  else delete root.dataset.theme;
  document.cookie = themeCookie(choice, window.location.protocol === "https:");
  window.setTimeout(() => root.classList.remove("theme-switching"), 300);
}

export function ThemeToggle({ initial }: { initial: ThemeChoice }) {
  const [choice, setChoice] = useState<ThemeChoice>(initial);
  return (
    <div className="theme-toggle" role="group" aria-label={m.theme.label}>
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          aria-pressed={choice === value}
          aria-label={label}
          title={label}
          onClick={() => { setChoice(value); applyTheme(value); }}
        >
          <Icon size={14} strokeWidth={1.8} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
