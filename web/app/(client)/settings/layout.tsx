// Settings (redesign 2026-11-07): every workspace setting behind one nav item, as tabs. Models,
// Auto refresh, Team and Support access used to be separate pages; their old
// addresses redirect here. Team and Support access are for administrators, so only they see
// those two tabs; anyone who opens their addresses still gets the page's own "Only your
// administrator" text. If the role cannot be read the tabs stay, and each page decides.
import type { Me } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { PageTabs } from "@/ui/PageTabs";

export const dynamic = "force-dynamic";

const TABS = [
  { href: "/settings", label: "Models" },
  { href: "/settings/auto-refresh", label: "Auto refresh" },
  { href: "/settings/team", label: "Team", admin: true },
  { href: "/settings/support-access", label: "Support access", admin: true },
];

export default async function SettingsLayout({ children }: { children: React.ReactNode }) {
  let isAdmin = true;
  try {
    const me = await serverGet<Me>("/v1/me");
    if (me) isAdmin = me.role === "admin";
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e; // a sign-in redirect must go through
  }
  const tabs = TABS.filter((t) => isAdmin || !t.admin).map(({ href, label }) => ({ href, label }));
  return (
    <div className="pg">
      <h1>Settings</h1>
      <PageTabs tabs={tabs} label="Settings" />
      <div className="settings-body">{children}</div>
    </div>
  );
}
