export const PROVIDERS = [
  { code: "p_search", name: "Parallel", credential: "P_SEARCH_API_KEY" },
  { code: "p_scrape", name: "Firecrawl", credential: "P_SCRAPE_API_KEY" },
  {
    code: "p_signals",
    name: "PredictLeads",
    credential: "P_SIGNALS_API_KEY + P_SIGNALS_API_TOKEN",
  },
  { code: "p_people", name: "BitScale", credential: "P_PEOPLE_API_KEY" },
  { code: "p_org", name: "Sumble", credential: "P_ORG_API_KEY" },
] as const;

const PROVIDER_NAMES: Readonly<Record<string, string>> = Object.fromEntries(
  PROVIDERS.map(({ code, name }) => [code, name]),
);

export function providerName(code: string): string {
  return PROVIDER_NAMES[code] ?? code;
}
