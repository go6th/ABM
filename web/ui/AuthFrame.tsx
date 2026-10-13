// The sign-in frame for both planes: one centred column, no stage, no illustration.
import type { ReactNode } from "react";

export function AuthFrame({ children }: { plane?: "client" | "operator"; children: ReactNode }) {
  return (
    <main className="auth" id="main">
      <div className="auth-card" aria-labelledby="auth-title">{children}</div>
    </main>
  );
}

export function AuthBrand({ plane }: { plane: "client" | "operator" }) {
  return (
    <p className="brand">
      {plane === "operator" ? "6th admin" : "Account Intelligence"}
      <span className="small">{plane === "operator" ? "Account Intelligence" : "by 6th"}</span>
    </p>
  );
}
