// New research lives on the Research page as a pop-up, and importing a file on Accounts
// (2026-11-08); the old address still works and keeps what it asked for.
import { redirect } from "next/navigation";

export default async function Moved({ searchParams }: { searchParams: Promise<{ tab?: string; mode?: string }> }) {
  const q = await searchParams;
  const file = q.tab === "file" || q.mode === "file";
  redirect(file ? "/accounts/import" : "/research?start=1");
}
