// Home: one question box across all accounts, then at most five real changes since the user last
// looked. Data comes from the API through the server-side fetch helper.
import Link from "next/link";
import type { AccountPage, SignalPage } from "@/lib/gen/api.gen";
import { serverGet, ServerApiError } from "@/lib/server-api";
import { HomeAsk } from "@/components/ws/HomeAsk";
import { GradeMark, toGrade } from "@/components/ws/GradeMark";
import { LoadError } from "@/components/ws/States";
import { m } from "@/lib/messages";
import { ago, humanize } from "@/lib/ws-format";
import { cellText } from "@/lib/render";

export const dynamic = "force-dynamic";

export default async function Home() {
  let accounts: AccountPage | null = null;
  let signals: SignalPage | null = null;
  let reference: string | null | undefined;
  try {
    [accounts, signals] = await Promise.all([
      serverGet<AccountPage>("/v1/accounts?limit=200"),
      serverGet<SignalPage>("/v1/signals"),
    ]);
  } catch (e) {
    if (!(e instanceof ServerApiError)) throw e;
    reference = e.reference;
  }
  const names = new Map((accounts?.accounts ?? []).map((a) => [a.domain, a.name ?? a.domain]));
  const recent = (signals?.items ?? []).filter((s) => !s.sensitive).slice(0, 5);
  const hasAccounts = (accounts?.accounts.length ?? 0) > 0;

  return (
    <div className="ws-column home">
      <h1 className="ws-display home-title">{m.home.title}</h1>
      {reference !== undefined ? <LoadError reference={reference} retryHref="/" /> : null}
      <HomeAsk hasAccounts={hasAccounts} />
      {hasAccounts ? (
        <section className="ws-section" aria-labelledby="since">
          <div className="ws-section-head">
            <h2 id="since">{m.home.since}</h2>
            {recent.length ? <Link className="ws-link ws-small" href="/alerts">{m.home.seeAll}</Link> : null}
          </div>
          {recent.length ? (
            <ul className="ws-rows">
              {recent.map((s) => {
                const grade = toGrade(s.grade);
                return (
                  <li key={s.event_id}>
                    <Link className="ws-row" href={`/accounts/${encodeURIComponent(s.account)}`}>
                      <div>
                        <p className="name">{cellText(names.get(s.account) ?? s.account)}</p>
                        <p className="context">{humanize(s.signal_type)}</p>
                      </div>
                      <div className="meta">
                        {grade ? <GradeMark grade={grade} /> : null}
                        <span>{ago(s.detected_at)}</span>
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : <p className="ws-muted">{m.home.sinceEmpty}</p>}
        </section>
      ) : null}
    </div>
  );
}
