"use client";

import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function currentTheme(): Theme {
  const saved = document.documentElement.dataset.theme;
  return saved === "dark" || saved === "light" ? saved : systemTheme();
}

function subscribeTheme(listener: () => void) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", listener);
  window.addEventListener("storage", listener);
  window.addEventListener("abm-theme-change", listener);
  return () => {
    media.removeEventListener("change", listener);
    window.removeEventListener("storage", listener);
    window.removeEventListener("abm-theme-change", listener);
  };
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribeTheme, currentTheme, () => "light");

  const choose = (next: Theme) => {
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("abm-theme", next);
    } catch {
      // Theme still applies for this page when storage is unavailable.
    }
    window.dispatchEvent(new Event("abm-theme-change"));
  };

  return (
    <div className="theme-toggle" role="group" aria-label="Color theme">
      <button type="button" aria-pressed={theme === "light"} onClick={() => choose("light")}>
        <Sun aria-hidden="true" /> <span>Light</span>
      </button>
      <button type="button" aria-pressed={theme === "dark"} onClick={() => choose("dark")}>
        <Moon aria-hidden="true" /> <span>Dark</span>
      </button>
    </div>
  );
}
