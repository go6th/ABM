// The 6th admin console. Staff identity comes from the backend-verified session
// (OperatorSessionGate); the frame is AdminShell.
import { AdminShell } from "./AdminShell";
import { OperatorSessionGate } from "./OperatorSessionGate";

export default function OperatorConsoleLayout({ children }: { children: React.ReactNode }) {
  return <OperatorSessionGate><AdminShell>{children}</AdminShell></OperatorSessionGate>;
}
