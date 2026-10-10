"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { clearSession } from "@/lib/api";

export function SignOutButton() {
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  const handleSignOut = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
    } catch {
      // Proceed with client cleanup
    }

    clearSession();
    router.push("/login");
    router.refresh();
  };

  return (
    <button
      type="button"
      className="btn-signout"
      onClick={handleSignOut}
      disabled={loggingOut}
      title="Sign out of workspace"
      aria-label="Sign out of workspace"
    >
      <LogOut size={15} aria-hidden="true" />
      <span>{loggingOut ? "Signing out…" : "Sign out"}</span>
    </button>
  );
}
