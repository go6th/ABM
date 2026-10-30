"""Clean a captured page into the article it carries (archive record, step 1).

A captured page arrives as markdown-ish text with the site's chrome around the article: menus, a
search box, a language picker, share links, badges, a footer; the article itself is sometimes there
twice, and a table is sometimes flattened into one line. ``clean_page`` returns only the article:
its body as paragraphs and ``##`` headings, its tables as ``columns`` + ``rows``, its commentary and
its disclaimer as separate parts. Rule-based and offline: no provider, no model, no network. The
rules (stop-lists, footer markers, per-publisher chrome) are ``config/archive_clean.yaml``.

Pure functions. ``check_clean`` is the validation of the cleaned text (step 7) and is the same
check the stored record is held to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from account_intelligence.config import CONFIG_DIR
from jobs.config import load_yaml

_PIPE = "\x00"  # an escaped pipe (\|) while the text is split into cells
_TABLE = "\x01"  # a table placeholder line while the text is cleaned
_SEP = re.compile(r"(?:\|\s*:?-{3,}:?\s*)+\|?")
_SEP_LINE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_EMPTY_LINK = re.compile(r"\[\s*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_ANGLE_URL = re.compile(r"<https?://[^>\s]+>")
_BARE_URL_LINE = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$")
_ESCAPED = re.compile(r"\\([#_*\[\]()>`~.!+\-{}<=])")
_BOLD_LINE = re.compile(r"^(?:\*\*|__)\s*(.{2,90}?)\s*:?\s*(?:\*\*|__)\s*:?$")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
_LIST_ITEM = re.compile(r"^(?:[-*•]|\d{1,2}[.)])\s+\S")
_WORD = re.compile(r"[\w'’-]+", re.UNICODE)
# minimum words for a repeated block to count as a copy (step 1)
DUP_WORDS = 30
DUP_SIMILARITY = 0.8
# words the body must hold before a footer marker may cut the rest
FOOTER_AFTER_WORDS = 30


@dataclass
class Cleaned:
    """The parts of a cleaned page."""

    body: str = ""
    tables: list[dict[str, Any]] = field(default_factory=list)
    analysis: list[dict[str, str]] = field(default_factory=list)
    disclaimer: str = ""
    # the line under the headline that names the publisher and the date ("Reuters · 21 Sep 2026")
    byline: str = ""
    language: str = "und"
    boilerplate_removed: int = 0
    duplicates_removed: int = 0
    tables_repaired: int = 0

    @property
    def words(self) -> int:
        """Words in the body."""
        return len(_WORD.findall(self.body))


class _Rules:
    """The compiled rules of ``config/archive_clean.yaml``."""

    def __init__(self, raw: dict[str, Any]) -> None:
        def rx(items: list[str]) -> list[re.Pattern[str]]:
            return [re.compile(p, re.IGNORECASE) for p in items]

        self.raw = raw
        self.chrome = rx(raw["chrome_lines"])
        self.footer = rx(raw["footer_markers"])
        self.disclaimer = rx(raw["disclaimer_markers"])
        self.analysis = rx(raw["analysis_headings"])
        self.publisher = {
            host: rx(spec.get("chrome_lines", [])) for host, spec in raw["publishers"].items()
        }

    def chrome_for(self, host: str) -> list[re.Pattern[str]]:
        """The site-wide chrome patterns plus those of the publisher's host."""
        extra = [p for h, ps in self.publisher.items() if on_host(host, h) for p in ps]
        return [*self.chrome, *extra]


@lru_cache(maxsize=1)
def rules() -> _Rules:
    """The rules, loaded once."""
    raw, _ = load_yaml(CONFIG_DIR / "archive_clean.yaml")
    return _Rules(raw)


def config() -> dict[str, Any]:
    """The raw archive cleaning configuration (the classifier and fact extractor read lists)."""
    return rules().raw


def on_host(host: str, domain: str) -> bool:
    """True when ``host`` is ``domain`` or one of its subdomains."""
    host = host.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def words_of(text: str) -> list[str]:
    """The words of a text, lower-cased."""
    return [w.lower() for w in _WORD.findall(text)]


def _shingles(words: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def unescape(text: str) -> str:
    """Page text with markdown escapes removed (``\\#`` -> ``#``), non-breaking and zero-width
    spaces turned into plain ones, and line endings made ``\\n``. An escaped pipe stays a marker
    until the tables are read."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u00a0\u2007\u202f\u200b\u200c\u200d\ufeff]", " ", text)
    text = text.replace("\\|", _PIPE)
    text = _ESCAPED.sub(r"\1", text)
    return re.sub(r"[ \t]+", " ", text)


def strip_links(text: str) -> str:
    """Images and empty links removed, a link reduced to its text, an angle-bracket address
    dropped."""
    text = _IMAGE.sub("", text)
    text = _EMPTY_LINK.sub("", text)
    text = _LINK.sub(r"\1", text)
    return _ANGLE_URL.sub("", text)


def _cell(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = value.replace("**", "").replace("__", "").replace(_PIPE, "|")
    return " ".join(value.split())


def _label_of(prose: str) -> tuple[str, str]:
    """The prose before a table split into the prose and the table's title: a bold label, a
    heading line or a short line without a full stop that ends the prose."""
    stripped = prose.rstrip()
    bold = re.search(r"(?:^|\s)(?:\*\*|__)([^*_\n]{2,70}?)(?:\*\*|__)\s*:?$", stripped)
    if bold:
        return stripped[: bold.start()].rstrip(), bold.group(1).strip()
    last = stripped.rsplit("\n", 1)
    tail = last[-1].strip()
    heading = _HEADING.match(tail)
    if heading:
        return (last[0] if len(last) == 2 else ""), heading.group(1).strip()
    if len(last) == 2 and 1 <= len(tail.split()) <= 7 and not re.search(r"[.!?]$", tail):
        return last[0], tail.rstrip(":").strip()
    return stripped, ""


def _flat_table(group: str) -> tuple[str, dict[str, Any] | None, str]:
    """A table flattened into one line (``|# |Detail | | --- | --- | |1 |a |b |``) rebuilt:
    ``(prose before, table, prose after)``; the table is None when the line is not one."""
    match = _SEP.search(group)
    if match is None or group.find("|") < 0 or group.find("|") > match.start():
        return group, None, ""
    n = len(re.findall(r":?-{3,}:?", match.group(0)))
    if n < 2:
        return group, None, ""
    before = group[: match.start()]
    first = before.find("|")
    prose, head = before[:first], before[first:]
    columns = [_cell(c) for c in head.split("|") if c.strip()]
    if len(columns) > n:
        prose = (prose + " " + " ".join(columns[: len(columns) - n])).strip()
        columns = columns[len(columns) - n :]
    if len(columns) != n:
        return group, None, ""
    tokens = group[match.end() :].strip().split("|")
    if tokens and tokens[0] == "":
        tokens = tokens[1:]
    rows: list[list[str]] = []
    i = 0
    # LOOP(flattened table rows): cap=the cells of one line; exit=fewer cells than a row
    while i + n <= len(tokens) - 1:
        rows.append([_cell(c) for c in tokens[i : i + n]])
        i += n
        if i < len(tokens) - 1 and not tokens[i].strip():
            i += 1
    rest = [t for t in tokens[i:]]
    after = ""
    if len(rest) == 1:
        after = _cell(rest[0])
    elif rest:  # a row cut short: kept, padded
        cells = [_cell(c) for c in rest if c.strip()]
        if cells:
            rows.append((cells + [""] * n)[:n])
    prose, title = _label_of(prose)
    return prose, {"title": title, "columns": columns, "rows": rows}, after


def _line_table(lines: list[str]) -> tuple[list[str], dict[str, Any] | None, list[str]]:
    """A multi-line pipe table (header, separator, rows): ``(lines before, table, lines after)``."""
    for i, line in enumerate(lines):
        if i == 0 or not _SEP_LINE.match(line) or "-" not in line:
            continue
        head = lines[i - 1]
        if "|" not in head:
            continue
        columns = [_cell(c) for c in head.strip().strip("|").split("|")]
        rows: list[list[str]] = []
        j = i + 1
        # LOOP(table rows): cap=the lines of one block; exit=a line without a pipe
        while j < len(lines) and "|" in lines[j]:
            cells = [_cell(c) for c in lines[j].strip().strip("|").split("|")]
            rows.append((cells + [""] * len(columns))[: len(columns)] if cells else [])
            j += 1
        before, title = _label_of("\n".join(lines[: i - 1]))
        table = {"title": title, "columns": columns, "rows": rows}
        return before.split("\n") if before else [], table, lines[j:]
    return lines, None, []


def repair_tables(text: str) -> tuple[str, list[dict[str, Any]], int]:
    """The text with each table replaced by a placeholder line, the tables as ``columns`` +
    ``rows`` and how many were rebuilt from a flattened line. A table is either consecutive pipe
    lines (header, separator, rows) or one line holding a whole flattened table."""
    tables: list[dict[str, Any]] = []
    repaired = 0
    out: list[str] = []

    def put(table: dict[str, Any]) -> None:
        if not table["title"]:
            # LOOP(trailing blanks): cap=the lines already kept; exit=a non-blank line
            while out and not out[-1].strip():
                out.pop()
            if out and not out[-1].startswith(_TABLE):
                rest, label = _label_of(out[-1])
                if label and not rest.strip():
                    table["title"] = label
                    out.pop()
        tables.append(table)
        out.append(f"{_TABLE}{len(tables) - 1}{_TABLE}")

    lines = text.split("\n")
    i = 0
    # LOOP(table scan): cap=the lines of the page (i advances every pass); exit=end of the lines
    while i < len(lines):
        if "|" not in lines[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        # LOOP(pipe run): cap=the lines of the page; exit=a line without a pipe
        while j < len(lines) and "|" in lines[j]:
            j += 1
        group = lines[i:j]
        i = j
        before, table, after = _line_table(group) if len(group) > 1 else (group, None, [])
        if table is not None:
            out.extend(before)
            put(table)
            out.extend(after)
            continue
        for line in group:
            prose, flat, tail = _flat_table(line)
            if flat is None:
                out.append(line)
                continue
            repaired += 1
            if prose.strip():
                out.append(prose.strip())
            put(flat)
            if tail.strip():
                out.append(tail.strip())
    return "\n".join(out), tables, repaired


def _norm(line: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", line.lower()).split())


def _is_short_nav(line: str) -> bool:
    """A line of at most three words that is no heading, list item or sentence."""
    return (
        bool(line.strip())
        and len(line.split()) <= 3
        and not line.startswith(("#", _TABLE))
        and not _LIST_ITEM.match(line)
        and not re.search(r"[.!?:;,]$", line)
    )


def _mark_headings(line: str) -> str:
    bold = _BOLD_LINE.match(line.strip())
    if bold:
        return "## " + bold.group(1).strip()
    head = _HEADING.match(line.strip())
    return "## " + head.group(1).strip() if head else line


def _plain(line: str) -> str:
    line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)
    line = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", line)
    return line.replace("**", "").replace("__", "")


def _drop_chrome(
    lines: list[str], title: str, host: str, stats: dict[str, int]
) -> tuple[list[str], str]:
    """The lines of the article: chrome lines and runs of short navigation lines removed, the
    text before the title cut when the title is found, a disclaimer line taken out. Returns the
    lines and the disclaimer."""
    chrome = rules().chrome_for(host)
    disclaimer: list[str] = []
    kept: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            kept.append("")
            continue
        if any(p.search(s) for p in rules().footer):
            kept.append(s)  # ``_cut_footer`` decides: it cuts the footer or drops the line
            continue
        if any(p.search(s) for p in chrome):
            stats["boilerplate"] += 1
            continue
        if _BARE_URL_LINE.match(s):
            stats["boilerplate"] += 1
            continue
        if (
            any(p.search(s) for p in rules().disclaimer)
            and len(s.split()) <= 80
            and not s.startswith(_TABLE)
        ):
            disclaimer.append(s)
            continue
        kept.append(s)
    # runs of three or more short, unpunctuated lines are menus
    # (blank lines between the items do not end a run)
    drop: set[int] = set()
    run: list[int] = []
    for i, line in enumerate([*kept, "x y z w"]):
        if not line.strip():
            continue
        if _is_short_nav(line):
            run.append(i)
            continue
        if len(run) >= 3:
            drop.update(run)
        run = []
    stats["boilerplate"] += len(drop)
    out = [line for i, line in enumerate(kept) if i not in drop]
    return _from_title(out, title, stats), " ".join(disclaimer)


def _from_title(lines: list[str], title: str, stats: dict[str, int]) -> list[str]:
    """The lines from the headline on (the text before it is the site's chrome); the headline
    itself and an immediate repeat of it are the title, not the body."""
    want = _norm(title)
    if not want:
        return lines
    body_words = sum(len(x.split()) for x in lines)
    for i, line in enumerate(lines):
        got = _norm(re.sub(r"^#+\s*", "", line))
        if got and (got == want or SequenceMatcher(None, got, want).ratio() >= 0.9):
            rest = lines[i + 1 :]
            if sum(len(x.split()) for x in rest) < 30 and body_words >= 30:
                return lines  # a title match that would leave nothing: leave the text alone
            stats["boilerplate"] += sum(1 for x in lines[:i] if x.strip()) + 1
            # LOOP(headline repeats): cap=the lines after the headline; exit=first other line
            while rest and (not rest[0].strip() or _norm(re.sub(r"^#+\s*", "", rest[0])) == want):
                rest = rest[1:]
            return rest
    return lines


def _cut_footer(lines: list[str], stats: dict[str, int]) -> list[str]:
    """The lines up to the footer: once the body holds ``FOOTER_AFTER_WORDS`` words, the first
    footer marker cuts the rest; a marker line before that is a menu item and is dropped alone."""
    seen = 0
    out: list[str] = []
    for i, line in enumerate(lines):
        if any(p.search(line) for p in rules().footer):
            if seen >= FOOTER_AFTER_WORDS:
                stats["boilerplate"] += sum(1 for x in lines[i:] if x.strip())
                return out
            stats["boilerplate"] += 1
            continue
        if not line.startswith(_TABLE):
            seen += len(line.split())
        out.append(line)
    return out


def _paragraphs(lines: list[str]) -> list[str]:
    """Blocks of the article: a heading or table is a block of its own; a line is a block of its
    own unless the line before it ends mid-sentence (a hard-wrapped paragraph), and list items
    keep their own lines."""
    blocks: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        wrapped = (
            blocks
            and not blocks[-1].startswith(("## ", _TABLE))
            and not _LIST_ITEM.match(s)
            and not re.search(r"[.!?:”\")]$", blocks[-1])
            and s[0].islower()
        )
        if s.startswith(("## ", _TABLE)):
            blocks.append(s)
        elif wrapped:
            blocks[-1] += " " + s
        elif _LIST_ITEM.match(s) and blocks and _LIST_ITEM.match(blocks[-1].split("\n")[0]):
            blocks[-1] += "\n" + s
        else:
            blocks.append(s)
    return blocks


def _dedupe(blocks: list[str]) -> tuple[list[str], int]:
    """The blocks with copies removed: a block of at least ``DUP_WORDS`` words that is at least
    ``DUP_SIMILARITY`` contained in what was already kept goes (the first stays); a heading
    already seen goes too."""
    seen_shingles: set[tuple[str, ...]] = set()
    seen_heads: set[str] = set()
    out: list[str] = []
    removed = 0
    for block in blocks:
        if block.startswith("## "):
            key = _norm(block)
            if key in seen_heads:
                removed += 1
                continue
            seen_heads.add(key)
            out.append(block)
            continue
        if block.startswith(_TABLE):
            out.append(block)
            continue
        words = words_of(block)
        mine = _shingles(words)
        if (
            len(words) >= DUP_WORDS
            and mine
            and len(mine & seen_shingles) / len(mine) >= DUP_SIMILARITY
        ):
            removed += 1
            continue
        seen_shingles |= mine
        out.append(block)
    return out, removed


_BYLINE = re.compile(
    r"(?:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|\b\d{1,2}\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b20\d\d-\d\d-\d\d\b)",
    re.IGNORECASE,
)


def _take_byline(blocks: list[str]) -> tuple[list[str], str]:
    """A short line near the top that names a date and has no sentence in it is the byline."""
    for i, block in enumerate(blocks[:3]):
        if block.startswith(("## ", _TABLE)) or len(block.split()) > 14:
            continue
        if _BYLINE.search(block) and not re.search(r"[a-z]{3,}[.!?]$", block):
            return blocks[:i] + blocks[i + 1 :], block
    return blocks, ""


def _split_analysis(blocks: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Blocks under a commentary heading ("AI Analyst View", "Why it matters") move out of the
    body into the analysis list."""
    body: list[str] = []
    analysis: list[dict[str, str]] = []
    heading = ""
    in_analysis = False
    for block in blocks:
        if block.startswith("## "):
            heading = block[3:]
            in_analysis = any(p.search(heading) for p in rules().analysis)
            if not in_analysis:
                body.append(block)
            continue
        if in_analysis and not block.startswith(_TABLE):
            analysis.append({"heading": heading, "text": block})
        else:
            body.append(block)
    return body, analysis


_STOP: dict[str, str] = {
    "en": "the and of to in is that for with as on are was by this from at be an or it has have",
    "de": "der die und das ist nicht ein eine mit von zu den für auf im dem auch sich werden wird",
    "fr": "le la les et des est un une dans pour que qui sur par pas au aux avec ses sont",
    "es": "el la los las y de que en un una para con por se su del al es como más",
    "it": "il lo la gli le e di che in un una per con non sono del della dei più come",
    "pt": "o a os as e de que em um uma para com por não do da dos das é como mais",
    "nl": "de het een en van in is dat op te voor met zijn niet aan er maar om ook als",
}


def detect_language(text: str) -> str:
    """The ISO code of the page's language by common-word counts (``und`` when it is not clear)."""
    words = words_of(text)[:600]
    if len(words) < 12:
        return "und"
    score = {code: sum(1 for w in words if w in set(stop.split())) for code, stop in _STOP.items()}
    best = max(score, key=lambda c: score[c])
    ordered = sorted(score.values(), reverse=True)
    if score[best] / len(words) < 0.08 or ordered[0] < ordered[1] * 1.3:
        return "und"
    return best


def clean_page(text: str, *, title: str = "", host: str = "") -> Cleaned:
    """The article of a captured page (see the module docstring)."""
    stats = {"boilerplate": 0}
    text = unescape(text)
    text, tables, repaired = repair_tables(strip_links(text))
    lines = [_plain(_mark_headings(x.strip())) for x in text.split("\n")]
    lines, disclaimer = _drop_chrome(lines, title, host, stats)
    lines = _cut_footer(lines, stats)
    blocks, removed = _dedupe(_paragraphs(lines))
    blocks, byline = _take_byline(blocks)
    blocks, analysis = _split_analysis(blocks)
    used = [int(b.strip(_TABLE)) for b in blocks if b.startswith(_TABLE)]
    kept_tables: list[dict[str, Any]] = []
    for idx in used:
        table = tables[idx]
        if any(
            t["columns"] == table["columns"] and t["rows"] == table["rows"] for t in kept_tables
        ):
            removed += 1
        else:
            kept_tables.append(table)
    body = "\n\n".join(b for b in blocks if not b.startswith(_TABLE)).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return Cleaned(
        body=body,
        tables=kept_tables,
        analysis=analysis,
        disclaimer=disclaimer,
        byline=byline,
        language=detect_language(body),
        boilerplate_removed=stats["boilerplate"],
        duplicates_removed=removed,
        tables_repaired=repaired,
    )


def check_clean(body: str, tables: list[dict[str, Any]]) -> list[str]:
    """What is wrong with a cleaned page, as short reasons (empty when it passes step 7): chrome
    left in the body, a repeated paragraph, a table whose rows differ in length, a markdown
    artifact the page would show raw."""
    problems: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if s and any(p.search(s) for p in rules().chrome):
            problems.append(f"chrome in body: {s[:60]}")
            break
    blocks = [b for b in re.split(r"\n\s*\n", body) if len(words_of(b)) >= DUP_WORDS]
    keys = [" ".join(words_of(b)) for b in blocks]
    if len(set(keys)) != len(keys):
        problems.append("a paragraph is repeated")
    for table in tables:
        width = len(table.get("columns") or [])
        if width < 2 or any(len(r) != width for r in table.get("rows") or []):
            problems.append("a table has rows of a different width")
            break
    shown = (
        body
        + " "
        + " ".join(
            " ".join([str(table.get("title", "")), *map(str, table.get("columns", []))])
            for table in tables
        )
    )
    for artifact in ("**", "\\#", "[](", _TABLE, _PIPE):
        if artifact in shown:
            problems.append(f"markdown artifact: {artifact!r}")
            break
    return problems
