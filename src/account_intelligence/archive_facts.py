"""Classify an archived page and pull typed facts out of it (archive record, steps 2 and 3).

Offline and rule-based: nothing is inferred beyond what a sentence states, an unknown value is
``"Not stated"`` (or ``"Not found"`` for an address), and an AI summary is never ``Confirmed``.
The host lists and the word lists are ``config/archive_clean.yaml``.

``classify`` reads the page as captured (its links still name the original); the fact functions
read the cleaned body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from account_intelligence.archive_clean import config, on_host

NOT_STATED = "Not stated"
NOT_FOUND = "Not found"
GRADES = ("Confirmed", "Probable", "Lead")
SOURCE_TYPES = (
    "Company press release",
    "Regulatory filing",
    "Company website",
    "Careers posting",
    "Trade press",
    "News aggregator",
    "AI-generated summary",
    "Earnings transcript",
    "Profile",
    "Podcast / video",
    "Blog / community",
    "Other",
)
EVENT_TYPES = (
    "Appointment",
    "Departure/Retirement",
    "Acquisition",
    "Divestiture",
    "Product launch",
    "Financial result",
    "Regulatory action",
    "Partnership",
    "Hiring program",
    "Other",
)

_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)"
    r"?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATES = [
    re.compile(
        rf"\b(?P<m>{_MONTH})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>20\d\d|19\d\d)\b"
    ),
    re.compile(
        rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{_MONTH})\.?,?\s+(?P<y>20\d\d|19\d\d)\b"
    ),
    re.compile(r"\b(?P<y>20\d\d)-(?P<mn>\d\d)-(?P<d>\d\d)\b"),
]
_END_OF_YEAR = re.compile(
    r"\b(?:at |by )?the end of (?:the (?:fiscal )?year |fiscal )?(?P<y>20\d\d)\b", re.I
)


def find_dates(text: str) -> list[tuple[str, int, int]]:
    """Every date the text states as ``(YYYY-MM-DD, start, end)`` in order of position. "The end of
    2026" is 31 December of that year."""
    found: list[tuple[str, int, int]] = []
    for rx in _DATES:
        for m in rx.finditer(text):
            g = m.groupdict()
            month = int(g["mn"]) if g.get("mn") else _MONTHS.get(g["m"].lower().rstrip("."), 0)
            try:
                iso = date(int(g["y"]), month, int(g["d"])).isoformat()
            except ValueError:
                continue
            found.append((iso, m.start(), m.end()))
    for m in _END_OF_YEAR.finditer(text):
        found.append((f"{m.group('y')}-12-31", m.start(), m.end()))
    return sorted(found, key=lambda x: x[1])


def _host(url: str) -> str:
    host = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _in(host: str, domains: list[str]) -> bool:
    return any(on_host(host, d) for d in domains)


@dataclass(frozen=True)
class Account:
    """Who the archive belongs to: the name the pages use and the account's own domain."""

    name: str = ""
    domain: str = ""

    def named_in(self, text: str) -> bool:
        """True when the text names the account (its name, or its domain's first label)."""
        label = self.domain.split(".")[0] if self.domain else ""
        for term in (self.name, label):
            if len(term) >= 2 and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.I):
                return True
        return False


@dataclass(frozen=True)
class Classification:
    """Step 2 of an archive record."""

    source_type: str
    is_primary: bool
    is_ai_generated: bool
    derived_from: dict[str, str] | None
    fact_grade: str


def _wire_in(text: str) -> str | None:
    for name, patterns in config()["wire_names"].items():
        if any(re.search(p, text) for p in patterns):
            return str(name)
    return None


def classify(
    url: str, *, kind: str, title: str, raw: str, account: Account | None = None
) -> Classification:
    """The source type, primacy, AI flag, origin and grade of a page. ``raw`` is the page as
    captured (its links still name the original release)."""
    account = account or Account()
    cfg = config()
    host = _host(url)
    path = urlsplit(url.strip()).path.lower()
    head = f"{title}\n{raw[:3000]}"
    ai = any(re.search(p, f"{title}\n{raw}") for p in cfg["ai_markers"])
    own = bool(account.domain) and on_host(host, account.domain.lower())
    wire = _wire_in(raw)
    if _in(host, cfg["profiles"]):
        stype = "Profile"
    elif _in(host, cfg["regulators"]) or re.search(
        r"\bform (?:8-k|10-[kq]|20-f|6-k)\b", head, re.I
    ):
        stype = "Regulatory filing"
    elif kind == "posting" or _in(host, cfg["careers"]) or re.search(r"/(?:careers?|jobs?)/", path):
        stype = "Careers posting"
    elif ai and not own:
        stype = "AI-generated summary"
    elif own:
        stype = (
            "Company press release"
            if re.search(r"/(?:news|press|media|newsroom|investors?|releases?)", path)
            else "Company website"
        )
    elif _in(host, cfg["wires"]):
        stype = "Company press release"
    elif _in(host, cfg["aggregators"]):
        stype = "News aggregator"
    elif re.search(r"(?:earnings|conference) call transcript|transcript:", title, re.I):
        stype = "Earnings transcript"
    elif _in(host, cfg["video"]):
        stype = "Podcast / video"
    elif _in(host, cfg["community"]):
        stype = "Blog / community"
    elif _in(host, cfg["trade_press"]):
        stype = "Trade press"
    else:
        stype = "Other"
    # primary: the company's own site, a release on a wire that names the company, a filing that
    # names it. Never an AI summary or an aggregator.
    named = account.named_in(head)
    primary = not ai and (
        own
        or (stype == "Company press release" and named)
        or (stype == "Regulatory filing" and named)
    )
    derived: dict[str, str] | None = None
    if stype in ("AI-generated summary", "News aggregator") or (wire and not primary and not own):
        origin = wire or NOT_STATED
        who = account.name or "the company"
        found = _original_url(raw, url)
        derived = {
            "name": f"{who} press release via {origin}" if wire else f"Original source of {who}",
            "url": found or NOT_FOUND,
        }
    if ai or stype in (
        "News aggregator",
        "Other",
        "Profile",
        "Podcast / video",
        "Blog / community",
    ):
        grade = "Lead"
    elif primary:
        grade = "Confirmed"
    elif stype in (
        "Trade press",
        "Regulatory filing",
        "Company press release",
        "Earnings transcript",
    ):
        grade = "Probable"
    else:
        grade = "Lead"
    return Classification(stype, primary, ai, derived, grade)


_URL = re.compile(r"https?://[^\s)\]>\"']+")


def _original_url(raw: str, own_url: str) -> str | None:
    """An address in the page that is on a wire domain (the release a summary was made from)."""
    wires = config()["wires"]
    for m in _URL.finditer(raw):
        link = m.group(0).rstrip(".,;")
        if link != own_url and _in(_host(link), wires) and urlsplit(link).path.strip("/"):
            return link
    return None


# --------------------------------------------------------------------------------- people

_NAME_TOKEN = r"(?:[A-Z][a-z]+(?:[-'’][A-Z][a-z]+)?|[A-Z]\.)"
_NAME = rf"{_NAME_TOKEN}(?:\s+(?:(?:de|van|von|da|del|di|la|le|bin)\s+)?{_NAME_TOKEN}){{1,3}}"
_NOT_NAME = {
    "chief",
    "officer",
    "executive",
    "president",
    "vice",
    "senior",
    "director",
    "board",
    "company",
    "corporation",
    "inc",
    "ltd",
    "llc",
    "group",
    "holdings",
    "global",
    "international",
    "the",
    "and",
    "of",
    "for",
    "new",
    "effective",
    "appoints",
    "names",
    "named",
    "announces",
    "announced",
    "promotes",
    "hires",
    "welcomes",
    "elects",
    "taps",
    "succeeds",
    "retires",
    "january",
    "february",
    "march",
    "april",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "technology",
    "information",
    "digital",
    "financial",
    "operating",
    "medical",
    "general",
    "head",
    "chair",
    "united",
    "states",
    "news",
    "press",
    "release",
    "newswire",
    "business",
    "wire",
    "its",
    "his",
    "her",
    "their",
    "she",
    "he",
    "they",
    "as",
    "to",
    "in",
    "at",
    "on",
    "by",
    "with",
    "from",
}


def person_name(candidate: str) -> str | None:
    """The candidate as a person's name (two to four name tokens, no role, month, company or verb
    word), else None."""
    tokens = candidate.split()
    # LOOP(leading non-name words): cap=the tokens of the candidate; exit=a name token
    while tokens and tokens[0].lower().rstrip(".") in _NOT_NAME:
        tokens = tokens[1:]
    # LOOP(trailing non-name words): cap=the tokens of the candidate; exit=a name token
    while tokens and tokens[-1].lower().rstrip(".") in _NOT_NAME:
        tokens = tokens[:-1]
    if not 2 <= len(tokens) <= 4:
        return None
    if any(t.lower().rstrip(".") in _NOT_NAME for t in tokens):
        return None
    if not all(
        re.fullmatch(_NAME_TOKEN, t)
        or t in ("de", "van", "von", "da", "del", "di", "la", "le", "bin")
        for t in tokens
    ):
        return None
    return " ".join(tokens)


def sentences(text: str) -> list[str]:
    """The sentences of a text (a full stop after a single capital, Inc, Corp, Mr and the like
    does not end one)."""
    flat = " ".join(text.split())
    parts = re.split(
        r"(?<=[.!?])(?<!\b[A-Z]\.)(?<!\bInc\.)(?<!\bCorp\.)(?<!\bCo\.)(?<!\bLtd\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bJr\.)(?<!\bSr\.)(?<!\bSept\.)(?<!\bU\.S\.)\s+(?=[A-Z“\"])",
        flat,
    )
    return [p.strip() for p in parts if p.strip()]


def _roles() -> str:
    return "|".join(re.escape(r) for r in config()["role_words"])


def _role(text: str) -> str | None:
    """A role phrase cut at the first clause break; it must hold a role word."""
    text = re.split(
        r"\s*(?:,|;|\.\s|\bwho\b|\beffective\b|\bsucceed|\breplac|\breport|\bstarting\b|\bbeginning\b|\bon\s+(?:"
        + _MONTH
        + r")|\bas of\b)",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(r"^(?:its|the|our|a|an)\s+", "", text.strip(), flags=re.I).strip()
    if 0 < len(text) <= 120 and re.search(rf"\b(?:{_roles()})\b", text, re.I):
        return text
    return None


def _titlecase_sentence(s: str) -> bool:
    """A headline: nearly every word capitalised (names inside it cannot be told from verbs)."""
    words = [w for w in s.split() if w[0].isalpha()]
    return len(words) >= 5 and sum(w[0].isupper() for w in words) / len(words) > 0.7


def _near_date(sentence: str, trigger: re.Pattern[str] | None = None) -> str | None:
    dates = find_dates(sentence)
    if not dates:
        return None
    if trigger:
        m = trigger.search(sentence)
        if m:
            after = [d for d in dates if d[1] >= m.start()]
            if after:
                return after[0][0]
    return dates[0][0] if len(dates) == 1 else None


_EFFECTIVE = re.compile(r"\b(?:effective|starting|beginning|as of|on)\b", re.I)
_APPOINT = re.compile(
    rf"(?P<n>{_NAME}),?\s+(?:has been |was |is |will be )?"
    rf"(?:appointed|named|promoted|elected|hired)\s+(?:as|to|its|the)\s+(?P<r>[^.]+)"
)
_JOIN = re.compile(
    rf"(?P<n>{_NAME})\s+(?:will\s+|is to\s+)?(?:join|joins|joined|become|becomes|serve|serves|"
    rf"take on)\s+"
    rf"(?:(?:\w+\s+){{0,4}}?as\s+|the (?:role|position) of\s+)(?P<r>[^.]+)"
)
_APPOINT_ACTIVE = re.compile(
    rf"\b(?:appoint|name|promote|hire|elect|tap|welcome)(?:s|d|ed)?\s+(?P<n>{_NAME})"
    rf"(?:,[^,]{{0,80}},)?\s+(?:as|to|its|the new)\s+(?P<r>[^.]+)",
    re.I,
)
_SUCCEED = re.compile(rf"\b(?:succeed(?:s|ing)?|replac(?:es|ing))\s+(?P<n>{_NAME})")
_RETIRE = re.compile(
    rf"(?P<n>{_NAME}),?(?:\s+[^,.]{{0,80}},)?\s+(?:who\s+)?(?:will\s+|is\s+|plans to\s+)"
    rf"?(?P<v>retir(?:e|es|ing)|step(?:s|ping)? down|depart(?:s|ing)?|resign(?:s|ing)?|leav(?:e|"
    rf"es|ing))"
)
_PREVIOUS = re.compile(
    r"\b(?:previously|most recently|formerly|prior to (?:joining|this))"
    r"\b[^.]{0,60}?\b(?:served as|was|held the (?:role|position) of|as)\s+(?P<r>[^.,;]+)",
    re.I,
)


def people_changes(
    body: str, *, announced: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(events, people)`` for appointments, departures and retirements the body states. A name
    must be a person's name; a role must hold a role word; the previous role is filled only when
    the body gives it."""
    events: list[dict[str, Any]] = []
    people: dict[str, dict[str, Any]] = {}
    announce = announced or NOT_STATED
    for sent in sentences(body):
        if _titlecase_sentence(sent):
            continue
        for rx in (_APPOINT_ACTIVE, _APPOINT, _JOIN):
            m = rx.search(sent)
            if not m:
                continue
            name, role = person_name(m.group("n")), _role(m.group("r"))
            if not name or not role:
                continue
            eff = _near_date(sent, _EFFECTIVE) or NOT_STATED
            prev = _PREVIOUS.search(sent)
            verb = "promoted" if re.search(r"\bpromot", sent, re.I) else "appointed"
            people[name] = {
                "name": name,
                "new_role": role,
                "previous_role": (_role(prev.group("r")) if prev else None) or NOT_STATED,
                "organization": NOT_STATED,
                "change_type": "Promoted" if re.search(r"\bpromot", sent, re.I) else "Appointed",
                "effective_date": eff,
            }
            events.append(
                {
                    "event_type": "Appointment",
                    "summary": f"{name} {verb} {role}",
                    "effective_date": eff,
                    "announced_date": announce,
                }
            )
            break
        succeeded = _SUCCEED.search(sent)
        if succeeded and (who := person_name(succeeded.group("n"))):
            people.setdefault(
                who,
                {
                    "name": who,
                    "new_role": NOT_STATED,
                    "previous_role": NOT_STATED,
                    "organization": NOT_STATED,
                    "change_type": "Succeeds",
                    "effective_date": NOT_STATED,
                },
            )
        left = _RETIRE.search(sent)
        if left and (who := person_name(left.group("n"))):
            retiring = left.group("v").lower().startswith("retir")
            eff = _near_date(sent, re.compile(left.group("v"), re.I)) or NOT_STATED
            row = people.setdefault(
                who,
                {
                    "name": who,
                    "new_role": NOT_STATED,
                    "previous_role": NOT_STATED,
                    "organization": NOT_STATED,
                    "change_type": "",
                    "effective_date": NOT_STATED,
                },
            )
            row.update(
                change_type="Retiring" if retiring else "Departed",
                effective_date=eff,
                new_role="Retiring" if retiring else "Departed",
            )
            events.append(
                {
                    "event_type": "Departure/Retirement",
                    "summary": f"{who} {'to retire' if retiring else 'departing'}",
                    "effective_date": eff,
                    "announced_date": announce,
                }
            )
    return events, list(people.values())


_EVENT_TRIGGERS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Acquisition",
        re.compile(
            r"\b(?:acquire[sd]?|to acquire|acquisition of|completes? (?:the )?acquisition|"
            r"agreed to buy)\b",
            re.I,
        ),
    ),
    (
        "Divestiture",
        re.compile(
            r"\b(?:divest(?:s|ed|iture|ment)?|spin[- ]?off|sale of (?:its|the)"
            r" [\w ]{3,40} business)\b",
            re.I,
        ),
    ),
    (
        "Product launch",
        re.compile(
            r"\b(?:launch(?:es|ed)?|unveil(?:s|ed)?|introduc(?:es|ed)|announces? (?:the )"
            r"?(?:availability|release) of)\b",
            re.I,
        ),
    ),
    (
        "Financial result",
        re.compile(
            r"\b(?:reports?|announces?|posted?) (?:[\w ,]{0,30})?(?:results|earnings|revenue|"
            r"quarter)\b|\bquarterly (?:results|earnings)\b",
            re.I,
        ),
    ),
    (
        "Regulatory action",
        re.compile(
            r"\b(?:FDA (?:approv|clear)\w*|recall|warning letter|consent decree|SEC (?:charge|"
            r"investigat)\w*|fined?|settle(?:s|d|ment)? with)\b",
            re.I,
        ),
    ),
    (
        "Partnership",
        re.compile(
            r"\b(?:partner(?:s|ed|ship)? with|collaborat(?:es|ion) with|strategic alliance|"
            r"joint venture)\b",
            re.I,
        ),
    ),
    (
        "Hiring program",
        re.compile(
            r"\b(?:hiring (?:\d[\d,]* |new |more )|will (?:hire|add) (?:\d[\d,]*)|recruit(?:ing|"
            r"ment) (?:drive|program))",
            re.I,
        ),
    ),
]


def other_events(title: str, body: str, *, announced: str | None) -> list[dict[str, Any]]:
    """One event per type the headline or the first sentences of the body state."""
    lead = [title, *sentences(body)[:4]]
    out: list[dict[str, Any]] = []
    for etype, rx in _EVENT_TRIGGERS:
        for sent in lead:
            if rx.search(sent):
                out.append(
                    {
                        "event_type": etype,
                        "summary": sent[:240],
                        "effective_date": _near_date(sent, _EFFECTIVE) or NOT_STATED,
                        "announced_date": announced or NOT_STATED,
                    }
                )
                break
    return out


# ----------------------------------------------------------------------------- organisations

_EXCH = r"(?:NYSE(?: American)?|NASDAQ|LSE|TSX|ASX|Euronext|NSE|BSE|XETRA|SIX)"
_ORG = re.compile(
    rf"(?P<o>(?:[A-Z][\w&.'’-]*,?[ ]+){{0,6}}(?:Inc\.?|Corp(?:oration|\.)?|Company|Co\.|Ltd\.?|"
    rf"LLC|plc|PLC|AG|SA|N\.V\.|Group|Holdings|Technologies))\s*\((?:(?P<x>{_EXCH})\s*:\s*)"
    rf"?(?P<t>[A-Z]{{1,6}})\)"
)
_ORG_EXCH = re.compile(
    rf"(?P<o>(?:[A-Z][\w&.'’-]*,?[ ]+){{1,6}}?(?:and[ ]+)?[A-Z][\w&.'’-]*)\s*\(\s*(?P<x>{_EXCH})"
    rf"\s*:\s*(?P<t>[A-Z]{{1,6}})\s*\)"
)


def organizations(
    title: str, body: str, raw: str, *, publisher: str, account: Account
) -> list[dict[str, Any]]:
    """The organisations the page names with their part in the story: the subject (the account, or
    the company a ticker follows), the wire that distributed the release, the publisher."""
    orgs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str, ticker: str | None, role: str) -> None:
        key = name.lower()
        if key in seen:
            for o in orgs:
                if o["name"].lower() == key and not o["ticker"]:
                    o["ticker"] = ticker
            return
        seen.add(key)
        orgs.append({"name": name, "ticker": ticker, "role_in_story": role})

    text = f"{title}\n{body}"
    for rx in (_ORG_EXCH, _ORG):
        for m in rx.finditer(text):
            name = m.group("o").strip().rstrip(",")
            ticker = f"{m.groupdict().get('x') or ''}:{m.group('t')}".lstrip(":")
            if rx is _ORG and not m.group("x") and not account.named_in(name):
                ticker = m.group("t")
            add(name, ticker, "Subject")
    unlisted = account.name.lower() not in seen and not any(
        account.named_in(o["name"]) for o in orgs
    )
    if account.name and account.named_in(text) and unlisted:
        add(account.name, None, "Subject")
    wire = _wire_in(raw)
    if wire:
        add(wire, None, "Distributor")
    if publisher:
        add(publisher, None, "Publisher")
    return orgs[:12]


# -------------------------------------------------------------------------------- figures

_FIG_METRIC = (
    "revenue",
    "sales",
    "net income",
    "operating income",
    "earnings per share",
    "EPS",
    "earnings",
    "margin",
    "growth",
    "cash flow",
    "guidance",
    "backlog",
    "bookings",
    "orders",
    "dividend",
    "headcount",
    "employees",
    "price",
)
_MONEY = re.compile(
    r"(?P<c>[$€£])\s?(?P<v>\d[\d,]*(?:\.\d+)?)\s?(?P<s>billion|million|thousand|bn|m|k)?\b", re.I
)
_PCT = re.compile(r"(?P<v>\d[\d,]*(?:\.\d+)?)\s?(?:%|percent)")
_PERIOD = re.compile(
    r"\b(?:Q[1-4]\s?(?:FY)?\s?\d{2,4}|(?:first|second|third|fourth) quarter(?: of)?(?: fiscal)"
    r"?(?: 20\d\d)?|fiscal (?:year )?20\d\d|full[- ]year (?:20\d\d)?|FY\s?\d{2,4})\b",
    re.I,
)
_UNIT = {"$": "USD", "€": "EUR", "£": "GBP"}


def figures(body: str) -> list[dict[str, str]]:
    """Money amounts and percentages stated beside a metric word (revenue, margin, headcount...)."""
    out: list[dict[str, str]] = []
    for sent in sentences(body):
        metric = next(
            (k for k in _FIG_METRIC if re.search(rf"\b{re.escape(k)}\b", sent, re.I)), None
        )
        if not metric:
            continue
        period = _PERIOD.search(sent)
        for m in _MONEY.finditer(sent):
            scale = (m.group("s") or "").lower()
            value = m.group("v") + (f" {scale}" if scale else "")
            out.append(
                {
                    "metric": metric,
                    "value": value,
                    "unit": _UNIT[m.group("c")],
                    "period": period.group(0) if period else NOT_STATED,
                }
            )
        for m in _PCT.finditer(sent):
            out.append(
                {
                    "metric": metric,
                    "value": m.group("v"),
                    "unit": "%",
                    "period": period.group(0) if period else NOT_STATED,
                }
            )
    return out[:12]


# ---------------------------------------------------------------------------------- quotes

_QUOTE_AFTER = re.compile(
    rf"[“\"](?P<q>[^”\"]{{25,600}}?)[,.]?[”\"]\s*,?\s*(?:said|says|added|noted|commented|stated)\s+(?P<s>{_NAME})"
)
_QUOTE_BEFORE = re.compile(
    rf"(?P<s>{_NAME}),?(?:\s+[^,.“\"]{{0,80}},)?\s+(?:said|says|added|noted|commented|stated)[:,]?\s+[“\"](?P<q>[^”\"]{{25,600}}?)[”\"]"
)


def quotes(body: str) -> list[dict[str, str]]:
    """Quotations with their speaker, verbatim from the body (a quote not found in the body as
    written is dropped)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for rx in (_QUOTE_AFTER, _QUOTE_BEFORE):
        for m in rx.finditer(body):
            quote = m.group("q").strip()
            speaker = person_name(m.group("s")) or NOT_STATED
            if quote in body and quote not in seen:
                seen.add(quote)
                out.append({"speaker": speaker, "quote": quote})
    return out[:10]


# --------------------------------------------------------------------- tags, lanes, summary


def tags_and_lanes(title: str, body: str) -> tuple[list[str], list[str]]:
    """The tags and lanes whose word patterns the headline or body states."""
    cfg = config()
    text = f"{title}\n{body[:6000]}"
    tags = [t for t, p in cfg["tags"].items() if re.search(p, text)]
    lanes = [t for t, p in cfg["lanes"].items() if re.search(p, text)]
    return tags, lanes


def lead_summary(body: str, *, limit: int = 320) -> str:
    """The first one to three sentences of the body that fit in ``limit`` characters (extracted,
    never written)."""
    out = ""
    for sent in sentences(re.sub(r"^## .*$", "", body, flags=re.M))[:3]:
        if len(out) + len(sent) + 1 > limit and out:
            break
        out = f"{out} {sent}".strip()
    return out[:limit].rstrip()


def published_from_text(raw: str, *, body: str = "") -> str | None:
    """A publication date the page states: after "Published", "Posted" or "Updated", in a wire
    dateline (``CITY, Sept. 21, 2026 /PRNewswire/``) or in the byline."""
    for rx in (
        re.compile(r"\b(?:published|posted|date|updated)\b[:\s-]{0,4}(?P<t>[^\n]{0,40})", re.I),
        re.compile(
            r"(?P<t>[A-Z][A-Za-z .,]{2,40},\s+[A-Z][a-z]+\.?\s+\d{1,2},\s+20\d\d)\s*(?:/|—|-)"
        ),
    ):
        for m in rx.finditer(raw[:4000]):
            dates = find_dates(m.group("t"))
            if dates:
                return dates[0][0]
    return None


def within_days(a: str, b: str, days: int = 3) -> bool:
    """True when two ISO dates are at most ``days`` apart (False if either is not a date)."""
    try:
        return abs(date.fromisoformat(a) - date.fromisoformat(b)) <= timedelta(days=days)
    except ValueError:
        return False
