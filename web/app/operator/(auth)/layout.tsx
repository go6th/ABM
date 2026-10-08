import Link from "next/link";

export default function OperatorAuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main id="main" className="auth-frame operator-auth-frame">
      <div className="operator-login-mark" aria-label="6th internal operator console">
        <span className="operator-mark" aria-hidden="true">6</span>
        <span><strong>6th</strong><small>INTERNAL SYSTEMS</small></span>
      </div>
      <div className="auth-card surface">{children}</div>
      <div className="auth-footer">
        <p className="muted auth-caption">Staff access only · all access is audited</p>
        <Link className="auth-panel-link" href="/login">Customer sign in</Link>
      </div>
    </main>
  );
}
