// Settings (redesign 2026-11-07): every workspace setting behind one nav item, as tabs. Models,
// Auto refresh, Team, Mail and drive and Support access used to be separate pages; their old
// addresses redirect here.
import { PageTabs } from "@/ui/PageTabs";

const TABS = [
  { href: "/settings", label: "Models" },
  { href: "/settings/auto-refresh", label: "Auto refresh" },
  { href: "/settings/team", label: "Team" },
  { href: "/settings/mail-and-drive", label: "Mail and drive" },
  { href: "/settings/support-access", label: "Support access" },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="pg">
      <h1>Settings</h1>
      <PageTabs tabs={TABS} label="Settings" />
      <div className="settings-body">{children}</div>
    </div>
  );
}
