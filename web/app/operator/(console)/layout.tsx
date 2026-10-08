import Link from "next/link";
import { OperatorNav } from "./OperatorNav";
import { OperatorSessionGate } from "./OperatorSessionGate";
import { OperatorSignOut } from "./OperatorSignOut";

export default function OperatorConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="operator-frame">
      <header className="operator-bar">
        <Link className="operator-lockup" href="/operator" aria-label="Operator console home">
          <span className="operator-mark" aria-hidden="true">6</span>
          <span><strong>6th</strong><small>OPERATOR CONSOLE</small></span>
        </Link>
        <div className="operator-bar-right">
          <span className="operator-pill"><span aria-hidden="true">●</span> STAFF AREA</span>
          <OperatorSignOut />
        </div>
      </header>
      <div className="operator-body">
        <aside className="operator-sidebar">
          <div className="operator-sidebar-label">PLATFORM</div>
          <OperatorNav />
          <div className="operator-sidebar-foot">
            <span className="operator-pulse" aria-hidden="true" />
            <span>Separate staff access</span>
          </div>
        </aside>
        <main id="main" className="operator-main"><OperatorSessionGate>{children}</OperatorSessionGate></main>
      </div>
    </div>
  );
}
