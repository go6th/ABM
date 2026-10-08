// CLI: computes WCAG contrast for every declared text-on-surface and icon-on-surface pair in both
// themes and exits 1 when any pair is below its minimum (4.5:1 body, 3:1 large text and UI).
// Usage: node design/check-contrast.ts
import { checkPairs } from "./contrast.ts";

const results = checkPairs();
const failed = results.filter((r) => !r.ok);
for (const r of failed) console.log(`FAIL ${r.theme} ${r.fg} on ${r.bg} (${r.note}): ${r.ratio} < ${r.minimum}`);
console.log(`contrast: ${results.length - failed.length} of ${results.length} pairs pass in light and dark`);
process.exit(failed.length ? 1 : 0);
