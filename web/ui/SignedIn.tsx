"use client";
// Renders children once the cookie session is known; sends to sign in otherwise.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { session, type Session } from "@/lib/api";
import { Loading } from "./primitives";

export function SignedIn({ children }: { children: (s: Session) => React.ReactNode }) {
  const router = useRouter();
  const [s, setS] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    session().then((v) => { if (!alive) return; if (!v) router.replace("/login"); else setS(v); }).catch(() => { if (alive) setS(null); });
    return () => { alive = false; };
  }, [router]);
  if (!s) return <Loading />;
  return <>{children(s)}</>;
}
