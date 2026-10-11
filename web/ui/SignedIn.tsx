"use client";
// Loads the session; sends the visitor to sign in when there is none. Children get the session.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, session, type Session } from "@/lib/api";
import { Banner, PageSkeleton } from "./primitives";

export function SignedIn({ children }: { children: (s: Session) => React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<Session | null | "loading" | "error">("loading");
  useEffect(() => {
    session()
      .then((s) => {
        if (s === null) router.push(`/login?return_to=${encodeURIComponent(window.location.pathname)}`);
        setState(s);
      })
      .catch((e: unknown) => setState(e instanceof ApiError && e.status === 401 ? null : "error"));
  }, [router]);
  if (state === "loading" || state === null) return <PageSkeleton />;
  if (state === "error") return <Banner kind="error">The service is unavailable. Try again shortly.</Banner>;
  return <>{children(state)}</>;
}
