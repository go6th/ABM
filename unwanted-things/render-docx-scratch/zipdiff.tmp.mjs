import JSZip from "jszip";
import { readFileSync } from "node:fs";
const [a, b] = process.argv.slice(2);
const za = await JSZip.loadAsync(readFileSync(a)), zb = await JSZip.loadAsync(readFileSync(b));
console.log(Object.keys(za.files).join(" "));
for (const n of Object.keys(za.files)) {
  if (za.files[n].dir) continue;
  const x = await za.files[n].async("string"); const y = await zb.files[n].async("string");
  if (x !== y) { let i = 0; while (x[i] === y[i]) i++; console.log("DIFF", n, "at", i, "\n  A:", JSON.stringify(x.slice(Math.max(0, i - 100), i + 60)), "\n  B:", JSON.stringify(y.slice(Math.max(0, i - 100), i + 60))); }
}
