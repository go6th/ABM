// Token meter: how many tokens a research run has used so far, as a grid where each square is
// 100,000 tokens (a square fills as the run passes each mark). Shown in the run panel, so it sits
// beside the run on the Run page and under the Ask box while a search is open.
export const TOKENS_PER_CELL = 100_000;
const ROW = 10;
const MAX_CELLS = 30;

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

export function TokenMeter({ tokens, running }: { tokens: number; running: boolean }) {
  const used = Math.max(0, Math.floor(tokens));
  const whole = Math.floor(used / TOKENS_PER_CELL);
  const part = (used % TOKENS_PER_CELL) / TOKENS_PER_CELL;
  const shown = Math.min(MAX_CELLS, Math.max(ROW, Math.ceil((whole + 1) / ROW) * ROW));
  const cells = Array.from({ length: shown }, (_, i) =>
    i < whole ? "full" : i === whole ? (part >= 0.75 ? "p75" : part >= 0.5 ? "p50" : part > 0 ? "p25" : "") : "");
  const next = (whole + 1) * TOKENS_PER_CELL;
  return (
    <div className="token-meter" role="group" aria-label="Tokens used by this research">
      <div className="token-meter-head">
        <span className="token-meter-total" aria-live="polite">{used.toLocaleString("en-US")} tokens</span>
        <span className="muted small">{running ? `next mark at ${next.toLocaleString("en-US")}` : "final count"}</span>
      </div>
      <div className="token-grid" aria-hidden="true">
        {cells.map((c, i) => <span key={i} className={`token-cell ${c}`} />)}
      </div>
      <p className="muted small">Each square is 100,000 tokens{whole >= MAX_CELLS ? `. ${formatTokens(used)} used in all` : ""}.</p>
    </div>
  );
}
