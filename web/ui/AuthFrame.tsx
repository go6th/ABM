// The sign-in frame for both planes: one centred column, no stage, no illustration.
import type { ReactNode } from "react";
import { Logo } from "@/ui/design";

export function AuthFrame({ plane = "client", children }: { plane?: "client" | "operator"; children: ReactNode }) {
  return (
    <main className={plane === "operator" ? "auth dark" : "auth"} id="main">
      <div className="auth-card" aria-labelledby="auth-title">{children}</div>
    </main>
  );
}

export function AuthBrand({ plane }: { plane: "client" | "operator" }) {
  return (
    <p className="brand">
      {plane === "operator" ? <Logo name="6th Admin" by="Account Intelligence" /> : <Logo />}
    </p>
  );
}
