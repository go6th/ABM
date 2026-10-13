// The 6th admin console frame: the same shell as the client workspace with its own navigation.
// Staff identity comes from the backend-verified session (OperatorSessionGate); nothing here
// names a client by anything but its id.
import Link from "next/link";
import { OperatorNav } from "./OperatorNav";
import { OperatorSessionGate } from "./OperatorSessionGate";
import { OperatorSignOut } from "./OperatorSignOut";

export default function OperatorConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="topbar"><b>6th admin</b><Link className="btn sm" href="/operator">Overview</Link></div>
      <aside className="sidebar" data-open="true">
        <Link className="brand" href="/operator">6th admin<span className="small">Account Intelligence</span></Link>
        <OperatorNav />
        <div className="foot"><OperatorSignOut /></div>
      </aside>
      <main id="main" className="main" tabIndex={-1}><div className="page"><OperatorSessionGate>{children}</OperatorSessionGate></div></main>
    </div>
  );
}
