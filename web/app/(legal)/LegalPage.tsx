// The public legal pages' frame (privacy policy, terms): readable without signing in, linked from
// sign-in and from Mail and drive, and named on the Google and Microsoft OAuth consent screens.
import Link from "next/link";
import { Logo } from "@/ui/design";

export function LegalPage({ title, updated, children }: { title: string; updated: string; children: React.ReactNode }) {
  return (
    <div className="legal">
      <header className="legal-head">
        <Link href="/" aria-label="Account Intelligence home"><Logo /></Link>
        <nav aria-label="Legal" className="legal-nav">
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href="/login">Sign in</Link>
        </nav>
      </header>
      <main id="main" className="legal-body">
        <h1>{title}</h1>
        <p className="legal-updated">Last updated: {updated}</p>
        {children}
      </main>
    </div>
  );
}
