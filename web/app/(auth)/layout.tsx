import { Suspense } from "react";
import { NavProgress } from "@/ui/NavProgress";

export default function ClientAuthLayout({ children }: { children: React.ReactNode }) {
  return <><Suspense fallback={null}><NavProgress /></Suspense>{children}</>;
}
