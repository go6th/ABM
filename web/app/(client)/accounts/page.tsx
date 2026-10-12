// Accounts: every account in this workspace as a row, search and sort, and Add account.
import { Suspense } from "react";
import type { AccountPage, Me } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { AccountList } from "@/components/ws/AccountList";
import { LoadError, PageHead } from "@/ui/primitives";
import { m } from "@/lib/messages";

export const dynamic = "force-dynamic";

export default async function Accounts() {
  let page: AccountPage | null = null;
  let me: Me | null = null;
  let reference: string | null | undefined;
  try {
    [page, me] = await Promise.all([serverGet<AccountPage>("/v1/accounts?limit=200", "/accounts"), serverGet<Me>("/v1/me", "/accounts")]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  const accounts = page?.accounts ?? [];
  const limit = me?.plan_limits.accounts ?? 0;
  return (
    <div className="content">
      <PageHead title={m.accounts.title} sub={m.accounts.sub(accounts.length)} />
      {reference !== undefined ? <LoadError reference={reference} retryHref="/accounts" /> : (
        <Suspense>
          <AccountList accounts={accounts} atCap={limit > 0 && accounts.length >= limit} canAdd={me?.capabilities.includes("runs:create") ?? false} />
        </Suspense>
      )}
    </div>
  );
}
