import JSZip from "jszip";
import { buildSummary } from "../src/summary.js";
import { packFixed } from "../src/pack.js";
import { makeBundle } from "../tests/synthetic.js";
const when = new Date("2026-10-19T00:00:00Z");
const a = await packFixed(buildSummary(makeBundle()), when);
const b = await packFixed(buildSummary(makeBundle()), when);
console.log("equal", a.equals(b), a.length, b.length);
const za = await JSZip.loadAsync(a), zb = await JSZip.loadAsync(b);
for (const n of Object.keys(za.files)) {
  const x = await za.files[n]!.async("string"); const y = await zb.files[n]!.async("string");
  if (x !== y) { let i = 0; while (x[i] === y[i]) i++; console.log("DIFF", n, i, JSON.stringify(x.slice(Math.max(0,i-80), i+80)), "|||", JSON.stringify(y.slice(Math.max(0,i-80), i+80))); }
}
