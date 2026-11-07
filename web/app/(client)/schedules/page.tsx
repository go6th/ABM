// Moved into Settings as Auto refresh (redesign 2026-11-07); the old address still works and keeps
// the account it pointed at.
import { redirect } from "next/navigation";

export default async function Moved({ searchParams }: { searchParams: Promise<{ account?: string }> }) {
  const account = (await searchParams).account;
  redirect(account ? `/settings/auto-refresh?${new URLSearchParams({ account }).toString()}#${encodeURIComponent(account)}` : "/settings/auto-refresh");
}
