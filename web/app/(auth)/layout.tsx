import Link from "next/link";
import { ShieldCheck } from "lucide-react";

export default function ClientAuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main id="main" className="auth-frame client-auth-frame">
      <Link className="wordmark auth-brand" href="/" aria-label="6th agents home">
        <span className="mark" aria-hidden="true">6</span>
        <span>6th agents</span>
        <span className="sub">Workspace</span>
      </Link>
      <div className="auth-card surface">{children}</div>
      <div className="auth-footer">
        <p className="muted auth-caption"><ShieldCheck aria-hidden="true" /> Secure access provided by your organization</p>
        <Link className="auth-panel-link" href="/operator/login">Staff sign in</Link>
      </div>
    </main>
  );
}
