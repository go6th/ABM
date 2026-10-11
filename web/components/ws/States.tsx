// Shared states: empty, error with retry and a reference, and content-shaped skeletons.
import Link from "next/link";
import { m } from "@/lib/messages";

export function Empty({ title, body, action }: { title: string; body: string; action?: { href: string; label: string } }) {
  return (
    <div className="ws-empty">
      <h3>{title}</h3>
      <p>{body}</p>
      {action ? <div className="actions"><Link className="ws-btn primary" href={action.href}>{action.label}</Link></div> : null}
    </div>
  );
}

export function LoadError({ reference, retryHref }: { reference: string | null; retryHref: string }) {
  return (
    <div className="ws-banner error" role="alert">
      <div className="grow">
        <p className="ws-label">{m.state.errorTitle}</p>
        <p className="ws-small">{m.state.errorBody}</p>
        {reference ? <p className="ws-small ws-muted">{m.state.reference(reference)}</p> : null}
      </div>
      <a className="ws-btn" href={retryHref}>{m.state.retry}</a>
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="ws-column" aria-busy="true">
      <span className="ws-sr" role="status">{m.state.loading}</span>
      <span className="ws-skel title" />
      <span className="ws-skel line" />
      <span className="ws-skel line short" />
      <span className="ws-skel line" />
    </div>
  );
}
