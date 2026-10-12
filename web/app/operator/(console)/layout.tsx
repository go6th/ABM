// The operator console frame: the same shell as the client workspace on the lilac plane. Staff
// identity comes from the backend-verified session (OperatorSessionGate); nothing here names a
// client.
import Link from "next/link";
import { ThemeToggle } from "@/ui/ThemeToggle";
import { currentTheme } from "@/ui/theme-server";
import { OperatorCrumb } from "./OperatorCrumb";
import { OperatorNav } from "./OperatorNav";
import { OperatorSessionGate } from "./OperatorSessionGate";
import { OperatorSignOut } from "./OperatorSignOut";

export default async function OperatorConsoleLayout({ children }: { children: React.ReactNode }) {
  const theme = await currentTheme();
  return (
    <div className="app" data-plane="operator">
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="app-sidebar">
        <Link className="brand" href="/operator" aria-label="Operator console home">
          <span className="brand-mark" aria-hidden="true">6</span>
          <span className="brand-name"><b>Operator console</b><span>6th staff</span></span>
        </Link>
        <OperatorNav />
        <div className="sidebar-foot">
          <div className="row"><span className="t-footnote ink-2">Appearance</span><ThemeToggle initial={theme} /></div>
        </div>
      </aside>
      <div className="app-column">
        <header className="app-toolbar">
          <Link href="/operator" className="app-mobile-brand" aria-label="Operator console home">
            <span className="brand-mark" aria-hidden="true">6</span>
            <span className="truncate">Operator</span>
          </Link>
          <OperatorCrumb />
          <div className="end"><OperatorSignOut /></div>
        </header>
        <main id="main" className="app-main" tabIndex={-1}><div className="wide"><OperatorSessionGate>{children}</OperatorSessionGate></div></main>
      </div>
    </div>
  );
}
