// Moved into Settings (redesign 2026-11-07). The OAuth callback still lands here with ?error=, so
// the code travels on to the new address.
import { redirect } from "next/navigation";

export default async function Moved({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const error = (await searchParams).error;
  redirect(error ? `/settings/mail-and-drive?${new URLSearchParams({ error }).toString()}` : "/settings/mail-and-drive");
}
