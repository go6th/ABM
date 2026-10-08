// Writes design/tokens.css from design/tokens.ts. Usage: npm run tokens
import { writeFileSync } from "node:fs";
import { tokensCss } from "./tokens-css.ts";

writeFileSync(new URL("./tokens.css", import.meta.url), tokensCss());
console.log("wrote design/tokens.css");
