import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const origin = process.env.API_ORIGIN ?? "http://127.0.0.1:8080";
  try {
    const response = await fetch(new URL("/readyz", origin), {
      cache: "no-store",
      signal: AbortSignal.timeout(2_000),
      redirect: "error",
    });
    return NextResponse.json(
      { ready: response.ok, api_status: response.status },
      { status: response.ok ? 200 : 503, headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { ready: false, api_status: "unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
