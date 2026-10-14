// The sign-in frame for both planes, as PDF page 1 draws it: the left column holds the logo, one
// white card and a line under it; the optional right column (`pitch`) holds a heading, a line of
// text and one card of three points. Without a pitch (forgot, reset, verify email, invite) the left
// column stands alone, centred. Below 1024px the right column moves under the card.
import type { ReactNode } from "react";
import { Grade, Logo } from "@/ui/design";

export function AuthFrame({
  plane = "client",
  pitch,
  below,
  children,
}: {
  plane?: "client" | "operator";
  pitch?: ReactNode;
  below?: ReactNode;
  children: ReactNode;
}) {
  const classes = ["auth", plane === "operator" ? "auth-admin" : "", pitch ? "auth-split" : "auth-solo"].filter(Boolean).join(" ");
  return (
    <div className={classes}>
      <main className="auth-main" id="main">
        <p className="auth-logo">
          {plane === "operator" ? <Logo name="6th Admin" by="Account Intelligence" inverse /> : <Logo />}
        </p>
        <div className="auth-card" aria-labelledby="auth-title">{children}</div>
        {below ? <p className="auth-below">{below}</p> : null}
      </main>
      {pitch}
    </div>
  );
}

type IconName = "shield" | "lock" | "document" | "chart" | "list" | "clock";

const ICONS: Record<IconName, ReactNode> = {
  shield: (
    <>
      <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
  document: (
    <>
      <path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M14 3v5h5" />
    </>
  ),
  chart: <path d="M5 20V10M12 20V4M19 20v-7M3 20h18" />,
  list: (
    <>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 9h6M9 13h6M9 17h4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l3 2" />
    </>
  ),
};

export interface PitchPoint {
  icon: IconName;
  title: string;
  body: string;
  grades?: boolean;
}

/** The right column: heading, one line of text and a card of points, each with an icon tile. */
export function AuthPitch({ title, lead, points }: { title: string; lead: string; points: PitchPoint[] }) {
  return (
    <aside className="auth-pitch" aria-labelledby="pitch-title">
      <div className="pitch-body">
        <h2 id="pitch-title">{title}</h2>
        <p className="pitch-lead">{lead}</p>
        <ul className="pitch-card">
          {points.map((p) => (
            <li key={p.title}>
              <span className="pitch-tile" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="20" height="20">{ICONS[p.icon]}</svg>
              </span>
              <div className="pitch-text">
                <h3>{p.title}</h3>
                <p>{p.body}</p>
                {p.grades ? (
                  <div className="pitch-grades">
                    <Grade grade="confirmed" />
                    <Grade grade="probable" />
                    <Grade grade="hypothesis" />
                    <Grade grade="lead" />
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

/** The key icon on "Continue with SSO". */
export function KeyIcon() {
  return (
    <svg className="key-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <circle cx="7.5" cy="16.5" r="3.5" />
      <path d="M10 14l9-9M16 8l2.5 2.5M14 10l1.5 1.5" />
    </svg>
  );
}

/** Google's "G" mark, in its four colours, for "Sign in with Google". */
export function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}
