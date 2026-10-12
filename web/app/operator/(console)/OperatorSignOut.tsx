"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";

export function OperatorSignOut() {
  const router = useRouter();
  const signOut = async () => {
    await fetch("/api/operator/session", { method: "DELETE", credentials: "same-origin", cache: "no-store" });
    router.replace("/operator/login");
  };
  return <button className="btn sm quiet" type="button" onClick={() => void signOut()}><LogOut size={16} aria-hidden="true" />Sign out</button>;
}
