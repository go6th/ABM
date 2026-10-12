// The caller's theme choice, read from the ai_theme cookie on the server (see ui/theme.ts).
import { cookies } from "next/headers";
import { THEME_COOKIE, parseTheme, type ThemeChoice } from "./theme";

export async function currentTheme(): Promise<ThemeChoice> {
  return parseTheme((await cookies()).get(THEME_COOKIE)?.value);
}
