// Provider display labels for the 6th operator console. Vendor names and credential variable names
// are internal: they are served only to a verified staff session and never compiled into a static
// chunk (everything under /_next/static is downloadable without signing in).
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { OperatorSession } from "@/lib/gen/api.gen";
import { googleOperatorOidcConfig, operatorApiOrigin, operatorOAuthCookieNames } from "@/lib/operator-oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROVIDERS = [
  { code: "p_search", name: "Parallel", credential: "P_SEARCH_API_KEY" },
  { code: "p_scrape", name: "Firecrawl", credential: "P_SCRAPE_API_KEY" },
  { code: "p_signals", name: "PredictLeads", credential: "P_SIGNALS_API_KEY + P_SIGNALS_API_TOKEN" },
  { code: "p_people", name: "BitScale", credential: "P_PEOPLE_API_KEY" },
  { code: "p_org", name: "Sumble", credential: "P_ORG_API_KEY" },
];

async function staffSession(): Promise<boolean> {
  const secure = googleOperatorOidcConfig()?.secureCookies ?? process.env.NODE_ENV === "production";
  const token = (await cookies()).get(operatorOAuthCookieNames(secure).session)?.value;
  const apiOrigin = operatorApiOrigin();
  if (!token || !apiOrigin) return false;
  try {
    const reply = await fetch(new URL("/operator/v1/session", apiOrigin), {
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      cache: "no-store",
      redirect: "manual",
    });
    return reply.ok && OperatorSession.safeParse(await reply.json()).success;
  } catch {
    return false;
  }
}

export async function GET(): Promise<NextResponse> {
  if (!(await staffSession())) {
    return NextResponse.json(
      { type: "urn:6th:problem:operator_session_required", title: "Staff sign-in required", status: 401 },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
  return NextResponse.json({ providers: PROVIDERS }, { headers: { "Cache-Control": "no-store" } });
}
