"""Person-name validation for the repair stage (spec: People, rule 8 "no fabrication: extracted
fragments are never names"). Pure and account-neutral: the word lists below are generic English
sentence words, verbs, job-title words and organisation words, never an account's people or
businesses.

``is_person_name`` accepts only a real-looking name: two to five tokens, letters of any script,
each token capitalised (or a lower-case surname particle such as "van" or "bin"), no digits, no
punctuation other than hyphen, apostrophe and the period of an initial. ``parse_person_name``
splits a descriptive phrase ("Jane Q. Rivera, chairman and CEO of Acme") into the name and the
title text that followed it, and returns ``None`` for a pure title, a quote or any sentence that
does not start with a name.
"""

from __future__ import annotations

import re
import unicodedata

# ---- word lists (module-level data: extend here, never in the functions) ------------------------
# lower-case surname particles; they may stand between two capitalised words
PARTICLES: tuple[str, ...] = (
    "de", "van", "von", "bin", "binti", "bint", "ibn", "al", "el", "da", "das", "dos", "do", "del",
    "della", "la", "le", "di", "du", "ter", "ten", "der", "den", "zu", "af", "dal", "des", "lo",
)  # fmt: skip
HONORIFICS: tuple[str, ...] = ("dr", "mr", "mrs", "ms", "mx", "miss", "prof", "sir", "dame")
SUFFIXES: tuple[str, ...] = ("jr", "sr", "ii", "iii", "iv")

_FUNCTION_WORDS: tuple[str, ...] = (
    "a", "an", "the", "and", "or", "but", "of", "at", "for", "with", "as", "to", "in", "on", "by",
    "from", "into", "about", "after", "before", "over", "under", "between", "than", "then",
    "that", "this", "these", "those", "which", "who", "whom", "whose", "what", "when", "where",
    "i", "we", "you", "he", "she", "they", "it", "me", "us", "him", "them", "my", "our", "your",
    "his", "her", "their", "its", "not", "no", "yes", "all", "any", "each", "some", "more",
    "most", "very", "just", "also", "now", "new", "former", "current", "interim", "acting",
    "emeritus", "retired", "think", "believe", "according", "per", "via", "etc",
)  # fmt: skip
VERB_WORDS: tuple[str, ...] = (
    "serves", "serve", "served", "serving", "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "will", "would", "can", "could", "should", "may", "might", "joined",
    "joins", "join", "joining", "named", "names", "appointed", "appoints", "elected", "promoted",
    "hired", "became", "becomes", "become", "remains", "remained", "leads", "led", "heads",
    "headed", "oversees", "oversaw", "runs", "ran", "reports", "reported", "said", "says",
    "announced", "announces", "announce", "stepped", "steps", "retires", "resigned", "resigns",
    "succeeds", "succeeded", "replaces", "replaced", "assumed", "assumes", "takes", "took",
    "holds", "held", "currently", "formerly", "previously", "expects", "expected", "thinks",
    "believes", "noted", "added", "stated", "confirmed", "commented",
)  # fmt: skip
TITLE_WORDS: tuple[str, ...] = (
    "chairman", "chairwoman", "chairperson", "chair", "chief", "president", "officer", "director",
    "vice", "head", "manager", "executive", "worldwide", "global", "regional", "senior",
    "junior", "principal", "deputy", "assistant", "associate", "general", "managing", "ceo",
    "cfo", "coo", "cio", "cto", "cmo", "cso", "ciso", "cdo", "cpo", "chro", "svp", "evp", "vp",
    "avp", "lead", "leader", "owner", "partner", "founder", "cofounder", "co-founder", "treasurer",
    "secretary", "controller", "counsel", "supervisor", "coordinator", "specialist", "engineer",
    "analyst", "consultant", "architect", "developer", "administrator", "dean", "trustee",
)  # fmt: skip
ROLE_WORDS: tuple[str, ...] = (
    "technology", "technologies", "information", "product", "products", "management",
    "operations", "engineering", "marketing", "sales", "finance", "financial", "digital", "data",
    "strategy", "transformation", "business", "development", "analytics", "security",
    "solutions", "services", "systems", "infrastructure", "enterprise", "customer", "commercial",
    "revenue", "platform", "platforms", "applications", "innovation", "procurement",
    "partnerships", "program", "programme", "project", "projects", "human", "resources",
    "communications", "legal", "supply", "chain", "quality", "research", "manufacturing",
    "clinical", "medical", "regulatory", "affairs", "compliance", "risk", "audit", "treasury",
    "accounting", "talent", "people", "experience", "growth", "channel", "alliances", "practice",
    "centre", "center", "excellence", "investor", "relations", "market", "markets", "care",
    "unit", "division", "department", "team", "office", "board", "committee", "council",
)  # fmt: skip
ORG_WORDS: tuple[str, ...] = (
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd", "limited", "llc", "plc",
    "gmbh", "holdings", "group", "partners", "associates", "industries", "international",
    "labs", "consulting", "capital", "ventures", "bank", "university", "institute", "foundation",
    "association", "society", "agency", "authority", "hospital", "clinic", "healthcare",
    "pharma", "pharmaceuticals", "network", "networks", "worldwide",
)  # fmt: skip
PLACEHOLDER_WORDS: tuple[str, ...] = (
    "name", "unknown", "anonymous", "tbd", "tba", "none", "null", "test", "sample", "example",
    "employee", "member", "user", "profile", "linkedin", "undisclosed", "redacted", "private",
)  # fmt: skip
# words that are also ordinary given names or surnames: kept out of the check when capitalised
NAME_LIKE_STOPWORDS: tuple[str, ...] = ("will", "said")

STOPWORDS: tuple[str, ...] = (
    _FUNCTION_WORDS + VERB_WORDS + TITLE_WORDS + ROLE_WORDS + ORG_WORDS + PLACEHOLDER_WORDS
)

# words that may stand between the name and the title in a sentence ("serves as the ...")
_LEAD_INS: tuple[str, ...] = (
    "serves", "serve", "served", "serving", "is", "are", "was", "were", "has", "have", "had",
    "been", "will", "be", "would", "currently", "also", "now", "still", "named", "appointed",
    "elected", "promoted", "hired", "became", "becomes", "remains", "remained", "as", "to", "the",
    "a", "an", "our", "its", "their", "new", "formerly", "previously", "assumed", "assumes",
)  # fmt: skip
_JOINED = ("joined", "joins", "join")
_DASHES = frozenset({"-", "–", "—", "|", "·", "•", ":"})
_APOSTROPHES = "'’ʼ"
_ALLOWED_MARKS = frozenset(f" -.{_APOSTROPHES}")
_INITIALS = re.compile(r"^(?:[^\W\d_]\.)+$")
_MAX_LENGTH = 80
_MAX_TOKEN = 30

_STOP = frozenset(STOPWORDS)
_NAME_LIKE = frozenset(NAME_LIKE_STOPWORDS)
_PARTICLES = frozenset(PARTICLES)
_VERBS = frozenset(VERB_WORDS)
_TITLES = frozenset(TITLE_WORDS)


def _letters_only(text: str) -> bool:
    """Every character is a letter or combining mark of any script, or one of ``- . '``."""
    for ch in text:
        if ch in _ALLOWED_MARKS:
            continue
        if unicodedata.category(ch)[0] not in ("L", "M"):
            return False
    return True


def _first_alpha(part: str) -> str:
    return next((ch for ch in part if ch.isalpha()), "")


def _cased_start(part: str) -> bool:
    """The first letter is upper case, or the script has no case (CJK, Arabic, Hebrew...)."""
    ch = _first_alpha(part)
    return bool(ch) and not ch.islower()


def _kind(token: str, index: int, last: int) -> str | None:
    """Classify one token: name, initial, particle, honorific, suffix, or ``None`` (invalid)."""
    if len(token) > _MAX_TOKEN or not _letters_only(token):
        return None
    bare = token.rstrip(".")
    low = bare.lower()
    if token == token.lower() and token in _PARTICLES and "." not in token:
        return "particle"
    head, _dash, tail = token.partition("-")
    if not _cased_start(token) and not (tail and head.lower() in _PARTICLES):
        return None
    if _INITIALS.match(token):
        return "initial"
    if low in HONORIFICS:
        return "honorific" if index == 0 else None
    if low in SUFFIXES and bare[:1].isupper():
        return "suffix" if index == last and index > 0 else None
    if len(bare) == 1 and bare.isupper():
        # a bare letter without a period is an initial only between two other words
        return "initial" if 0 < index < last else None
    if "." in token or token[0] in "-'’ʼ" or token[-1] in "-'’ʼ":
        return None
    if low in _STOP and not (low in _NAME_LIKE and token[0].isupper()):
        return None
    letters = [ch for ch in token if ch.isalpha()]
    if len(letters) >= 2 and token.isupper():
        return None  # SAP, CEO, VP: an acronym, never a name
    for part in token.split("-"):
        plow = part.lower()
        if not part:
            return None
        if plow in _PARTICLES:
            continue
        if not _cased_start(part) or (plow in _STOP and plow not in _NAME_LIKE):
            return None
    return "name"


def _kinds(tokens: list[str]) -> list[str] | None:
    last = len(tokens) - 1
    kinds: list[str] = []
    for i, tok in enumerate(tokens):
        k = _kind(tok, i, last)
        if k is None:
            return None
        kinds.append(k)
    return kinds


def is_person_name(text: str) -> bool:
    """Whether ``text`` reads like a person's name (2 to 5 tokens, no title, verb or organisation
    word, no digits or punctuation). A mononym is not valid."""
    if not isinstance(text, str):
        return False
    clean = text.strip()
    if not clean or len(clean) > _MAX_LENGTH or any(ch in "\n\r\t" for ch in clean):
        return False
    tokens = clean.split()
    if not 2 <= len(tokens) <= 5:
        return False
    kinds = _kinds(tokens)
    if kinds is None:
        return False
    if kinds[0] == "particle" or kinds[-1] == "particle":
        return False
    core = [k for k in kinds if k not in ("honorific", "suffix", "particle")]
    return len(core) >= 2 and core[-1] == "name"


def _tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def _strip_lead_in(rest: str) -> str:
    """Drop the verb group between a name and its title ("serves as the", "was named",
    "joined X as"). ``""`` when the verb is "joined" and no "as" follows."""
    toks = _tokens_with_spans(rest)
    if not toks:
        return ""
    if toks[0][0].lower() in _JOINED:
        for tok, _s, end in toks[1:]:
            if tok.lower() == "as":
                return rest[end:].strip()
        return ""
    i = 0
    # LOOP(strip lead-in words): cap=the words of the remainder; exit=a word that is not a lead-in
    while i < len(toks) and toks[i][0].lower().strip(",") in _LEAD_INS:
        i += 1
    return rest[toks[i][1] :].strip() if i < len(toks) else ""


def _is_title_phrase(text: str) -> bool:
    """Contains at least one job-title word and no quotation marks."""
    if not text or '"' in text or "“" in text or "”" in text:
        return False
    words = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", text.lower())
    return any(w in _TITLES for w in words)


def parse_person_name(text: str) -> tuple[str, str] | None:
    """Split ``"<Name>, <title>"`` or ``"<Name> serves as <title>"`` into ``(name, remainder)``.

    The leading 2 to 4 tokens must be a valid name, followed by a comma, a dash or colon, an
    opening parenthesis or a verb; what follows must be a title phrase (it contains a job-title
    word). Anything else, a bare title, a quote, a name without a title, returns ``None``."""
    if not isinstance(text, str):
        return None
    clean = " ".join(text.split())
    if not clean or len(clean) > 400:
        return None
    toks = _tokens_with_spans(clean)
    name_end = 0  # exclusive token index where the name stops
    rest_start = -1
    for i, (tok, _s, end) in enumerate(toks):
        low = tok.lower()
        if tok in _DASHES:
            name_end, rest_start = i, end
            break
        if tok[0] == "(":
            name_end, rest_start = i, toks[i][1]
            break
        if tok[-1] in ",;:":
            name_end, rest_start = i + 1, end
            break
        if low in _VERBS and tok == low and i > 0:
            name_end, rest_start = i, toks[i][1]
            break
        if i >= 4:
            return None
    else:
        return None
    if not 2 <= name_end <= 4:
        return None
    name_tokens = [t for t, _s, _e in toks[:name_end]]
    name_tokens[-1] = name_tokens[-1].rstrip(",;:")
    name = " ".join(name_tokens)
    if not is_person_name(name):
        return None
    rest = clean[rest_start:].strip(" ,;:-–—|·•")
    if rest.startswith("("):
        rest = rest.strip("()").strip()
    remainder = _strip_lead_in(rest) if rest else ""
    remainder = remainder.strip(" ,;:-–—|·•")
    if not _is_title_phrase(remainder):
        return None
    return name, remainder


# ---- structured names (a people provider's own name field) -----------------------------------
# A name field of a profile or people-search row is a name, not a sentence, but it can carry what
# a person adds to it: a nickname in brackets, trailing credentials, a trailing initial, a
# two-letter surname, a dash between surname parts. ``normalise_profile_name`` removes the first
# two and ``is_profile_name`` accepts the rest; text rows (news, phrases) use ``is_person_name``.
CREDENTIALS: tuple[str, ...] = (
    "mba", "ms", "msc", "ma", "mph", "mha", "mhs", "phd", "dphil", "md", "do", "dds", "dvm",
    "rn", "np", "pa", "cpa", "cfa", "cfp", "cma", "cia", "cisa", "cissp", "cism", "pmp", "pmi",
    "pe", "esq", "jd", "llm", "lssgb", "lssbb", "cssgb", "csm", "safe", "itil", "scrm", "cscp",
    "emba", "sphr", "phr", "shrm", "scp", "prince", "pgce", "acca", "ca", "mscm",
    "frm", "ccp", "cpim", "csp", "cap", "cgeit", "crisc", "mscm", "mpm", "me", "mengr", "bsc",
)  # fmt: skip
_CREDENTIAL_SET = frozenset(CREDENTIALS)
_BRACKETS = re.compile(r"\s*[(\[][^)\]]*[)\]]\s*")
_DASH_BETWEEN = re.compile(r"(?<=\w)\s+[-\u2010-\u2015]\s+(?=\w)")
# title words that are also first names count as a name in the first position only
_FIRST_NAME_OK = frozenset({"dean", "major", "duke", "earl", "chase", "chief"}) - {"chief"}
_BAD_WORDS = frozenset((*TITLE_WORDS, *ROLE_WORDS, *ORG_WORDS, *PLACEHOLDER_WORDS))


_SYMBOLS = re.compile(r"[^\w\s.'\u2019\-,()\[\]\"\u201c\u201d]")
_QUOTED = re.compile(r"\s*[\"\u201c][^\"\u201d]*[\"\u201d]\s*")
_CREDENTIAL_SEGMENT = re.compile(r"^[A-Za-z0-9().\-]{1,14}$")


def normalise_profile_name(raw: str) -> str:
    """The name without a bracketed or quoted nickname, symbols (emoji, registered marks) and
    trailing credentials ("Dana Q. Lee, MBA, PMP" is "Dana Q. Lee"; "Arthur (Art) Hu" is
    "Arthur Hu"); a dash between surname parts becomes a hyphen; a name typed in capitals or in
    lower case gets ordinary capitals."""
    text = _QUOTED.sub(" ", _BRACKETS.sub(" ", _SYMBOLS.sub("", raw or "")))
    text = _DASH_BETWEEN.sub("-", text)
    head, _comma, tail = text.partition(",")
    segments = [t.strip() for t in tail.split(",") if t.strip()]
    if segments and all(_CREDENTIAL_SEGMENT.match(t) for t in segments):
        text = head
    elif tail:
        return text.strip()  # a comma followed by words is a phrase, not a credential list
    words = text.split()
    # LOOP(drop trailing credentials): cap=the words of the name; exit=no credential word last
    while len(words) > 2 and _is_credential(words[-1]):
        words.pop()
    # a surname typed in capitals ("Hélène JEUNET") or a whole name in capitals gets ordinary
    # capitals; a one-letter word is an initial and stays
    words = [w.capitalize() if len(w) > 2 and w.isalpha() and w.isupper() else w for w in words]
    words = [
        w[0].upper() + w[1:] if w.islower() and w not in _PARTICLES and w not in _STOP else w
        for w in words
    ]
    return " ".join(words).strip()


def _is_credential(token: str) -> bool:
    return token.replace(".", "").strip(",").lower() in _CREDENTIAL_SET


def is_profile_name(text: str) -> bool:
    """Whether the name field of a profile row is a person's name: 2 to 5 tokens of letters
    (accents, hyphens, apostrophes, an initial with or without a period allowed), each starting
    upper case or in a script without case, no digits, and no title, role, organisation or
    placeholder word. Short surnames that are also common words ("He", "Will") are fine here."""
    if not isinstance(text, str):
        return False
    clean = text.strip()
    if not clean or len(clean) > _MAX_LENGTH or re.search(r"[\d\n\r\t:;/@|,]", clean):
        return False
    tokens = clean.split()
    if not 2 <= len(tokens) <= 5:
        return False
    for i, tok in enumerate(tokens):
        bare = tok.rstrip(".")
        if (
            not _letters_only(tok)
            or (bare.lower() in _BAD_WORDS and not (i == 0 and bare.lower() in _FIRST_NAME_OK))
            or len(tok) > _MAX_TOKEN
            or (tok == tok.lower() and tok not in _PARTICLES and _first_alpha(tok) != "")
            or (tok.isupper() and len(bare) > 1 and i > 0 and len(bare) > 2)
        ):
            return False
        if tok == tok.lower() and tok in _PARTICLES and i in (0, len(tokens) - 1):
            return False
    return not all(len(t.rstrip(".")) == 1 for t in tokens)
