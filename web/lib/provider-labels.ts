"use client";
// Provider labels for the operator console, fetched from /api/operator/providers (staff session
// only). Vendor names are deliberately absent from this module so they never ship in a public
// static chunk; until the labels arrive, or without a staff session, the provider code is shown.
import { useEffect, useState } from "react";

export interface ProviderLabel {
  code: string;
  name: string;
  credential: string;
}

let cached: Promise<ProviderLabel[]> | null = null;

function load(): Promise<ProviderLabel[]> {
  cached ??= fetch("/api/operator/providers", { cache: "no-store", credentials: "same-origin" })
    .then(async (reply) => {
      if (!reply.ok) throw new Error(String(reply.status));
      const body = (await reply.json()) as { providers?: ProviderLabel[] };
      return Array.isArray(body.providers) ? body.providers : [];
    })
    .catch(() => {
      cached = null; // retry on the next mount (for example after signing in again)
      return [];
    });
  return cached;
}

export function useProviderLabels(): { providers: ProviderLabel[]; providerName: (code: string) => string } {
  const [providers, setProviders] = useState<ProviderLabel[]>([]);
  useEffect(() => {
    let live = true;
    void load().then((value) => {
      if (live) setProviders(value);
    });
    return () => {
      live = false;
    };
  }, []);
  const names = new Map(providers.map(({ code, name }) => [code, name]));
  return { providers, providerName: (code: string) => names.get(code) ?? code };
}
