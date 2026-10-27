// The one text cleaner of the Word files, the same as the reference scripts' `cl`: an en or em
// dash with its spaces becomes a comma and a space, so does a spaced hyphen, a curly apostrophe
// becomes a straight one and an arrow becomes the word "to". Python has the same cleaner
// (parity.text.clean); the two must stay identical.
export function cl(x: unknown): string {
  return String(x ?? "")
    .replace(/\s*[–—]\s*/g, ", ")
    .replace(/ - /g, ", ")
    .replace(/’/g, "'")
    .replace(/→/g, " to ");
}

// A sentence from a bundle field: trimmed, and closed with a full stop when the writer left it
// open, so a subtitle built from several fields reads as prose.
export function sentence(s: string): string {
  const t = s.trim();
  return t === "" || /[.!?]$/.test(t) ? t : `${t}.`;
}

// "a", "a and b", "a, b and c".
export function joinAnd(parts: readonly string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
