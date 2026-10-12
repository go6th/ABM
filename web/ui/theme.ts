// Appearance: Light, Dark or System, kept in the ai_theme cookie so the server renders the
// right data-theme on <html> and there is no flash. "system" leaves the attribute off and the
// prefers-color-scheme block in ui/tokens.css applies. Not a secret and not tied to identity.
export type ThemeChoice = "light" | "dark" | "system";
export const THEME_COOKIE = "ai_theme";

export function parseTheme(value: string | undefined | null): ThemeChoice {
  return value === "light" || value === "dark" ? value : "system";
}

/** The data-theme attribute for a choice; undefined means "follow the system". */
export function themeAttribute(choice: ThemeChoice): "light" | "dark" | undefined {
  return choice === "system" ? undefined : choice;
}

export function themeCookie(choice: ThemeChoice, secure: boolean): string {
  return `${THEME_COOKIE}=${choice}; Path=/; Max-Age=31536000; SameSite=Lax${secure ? "; Secure" : ""}`;
}
