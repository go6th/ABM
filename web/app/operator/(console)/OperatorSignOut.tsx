"use client";

import { useRouter } from "next/navigation";

export function OperatorSignOut() {
  const router = useRouter();
  const signOut = async () => {
    await fetch("/api/operator/session", { method: "DELETE", credentials: "same-origin", cache: "no-store" });
    router.replace("/operator/login");
  };
  return <button className="link-btn small" type="button" onClick={() => void signOut()}>Sign out</button>;
}
