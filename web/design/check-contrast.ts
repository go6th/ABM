function luminance(hex: string) {
  let r = parseInt(hex.slice(1, 3), 16) / 255;
  let g = parseInt(hex.slice(3, 5), 16) / 255;
  let b = parseInt(hex.slice(5, 7), 16) / 255;
  r = r <= 0.03928 ? r / 12.92 : Math.pow((r + 0.055) / 1.055, 2.4);
  g = g <= 0.03928 ? g / 12.92 : Math.pow((g + 0.055) / 1.055, 2.4);
  b = b <= 0.03928 ? b / 12.92 : Math.pow((b + 0.055) / 1.055, 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(hex1: string, hex2: string) {
  const l1 = luminance(hex1);
  const l2 = luminance(hex2);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

import { tokens } from './tokens';

let failed = false;
for (const [theme, colors] of Object.entries(tokens)) {
  const bg = colors.canvas;
  const t = colors.textPrimary;
  const r = contrast(bg, t);
  if (r < 4.5) {
    console.error(`FAIL [${theme}]: canvas vs textPrimary = ${r.toFixed(2)} (needs 4.5)`);
    failed = true;
  }
}
if (failed) process.exit(1);
console.log("Contrast checks passed.");
