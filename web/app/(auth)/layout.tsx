import Link from "next/link";

export default function ClientAuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main id="main" className="auth-frame client-auth-frame">
      <Link className="wordmark" href="/" aria-label="6th agents home">
        <span className="mark" aria-hidden="true">6</span>
        <span>6th agents</span>
        <span className="sub">Workspace</span>
      </Link>
      <div className="auth-card surface">{children}</div>
      <p className="muted auth-caption">Secure access for your organisation</p>
    </main>
  );
}
