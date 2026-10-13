"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { OperatorSession } from "@/lib/gen/api.gen";
import { Loading, Notice } from "@/ui/primitives";

type GateState = { kind: "loading" } | { kind: "ready"; staffId: string; role: string } | { kind: "error"; message: string };

export interface OperatorViewer { staffId: string; role: string }
const OperatorViewerContext = createContext<OperatorViewer | null>(null);

export function useOperatorViewer(): OperatorViewer {
  const viewer = useContext(OperatorViewerContext);
  if (!viewer) throw new Error("Operator viewer is not available outside the staff console.");
  return viewer;
}

export function OperatorSessionGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [state, setState] = useState<GateState>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    fetch("/api/operator/session", { credentials: "same-origin", cache: "no-store" })
      .then(async (response) => {
        if (response.status === 401) {
          router.replace(`/operator/login?return_to=${encodeURIComponent(pathname)}`);
          return null;
        }
        if (!response.ok) throw new Error(response.status === 503 ? "Staff sign in is not configured." : "Access is unavailable.");
        return OperatorSession.parse(await response.json());
      })
      .then((session) => {
        if (alive && session) setState({ kind: "ready", staffId: session.staff_id, role: session.role });
      })
      .catch((error: unknown) => {
        if (alive) setState({ kind: "error", message: error instanceof Error ? error.message : "Access is unavailable." });
      });
    return () => { alive = false; };
  }, [pathname, router]);

  if (state.kind === "loading") return <Loading label="Checking staff access" />;
  if (state.kind === "error") return <Notice kind="error">{state.message}</Notice>;
  return (
    <OperatorViewerContext.Provider value={{ staffId: state.staffId, role: state.role }}>
      <p className="small muted" aria-label="Signed in staff identity">Signed in as <span className="mono">{state.staffId}</span> ({state.role === "operator" ? "Operator" : "Support"})</p>
      {children}
    </OperatorViewerContext.Provider>
  );
}
