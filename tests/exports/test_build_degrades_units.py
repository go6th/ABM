"""Pure parts of the degrade: the last-line sweep of a render model and the scrub of a workspace
member. Synthetic strings only."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.safety.client_output import scan_text, scrub_text
from exports.policy import leak_hits
from exports.sweep import sweep_model
from exports.verify import scrub_member
from tests.exports.corpus import broker

NOTE = "Sentence withheld."
OTHER = "Zorblax Industries"


class Row(BaseModel):
    """A frozen row like the render models."""

    model_config = ConfigDict(frozen=True)
    ref: str
    row_id: str
    text: str
    url: str = ""
    tags: tuple[str, ...] = ()


class Doc(BaseModel):
    """A frozen model holding rows and a dict."""

    model_config = ConfigDict(frozen=True)
    title: str
    rows: tuple[Row, ...]
    extra: dict[str, str] = {}


def blocked(text: str) -> str | None:
    """The test the builder passes: a restricted term is refused."""
    return "leak" if leak_hits(text, (OTHER,)) else None


def test_the_sweep_replaces_refused_strings_and_counts_them_by_rule() -> None:
    doc = Doc(
        title=f"About {OTHER}",
        rows=(
            Row(ref="R-0001", row_id="a", text="clean", tags=("x", f"{OTHER} tag")),
            Row(ref="R-0002", row_id="b", text=f"{OTHER} is named"),
        ),
        extra={"k": f"{OTHER}!", "ok": "fine"},
    )
    swept, report = sweep_model(doc, blocked, NOTE)
    assert swept.title == NOTE and swept.rows[1].text == NOTE
    assert swept.rows[0].text == "clean" and swept.rows[0].tags == ("x", NOTE)
    assert swept.extra == {"k": NOTE, "ok": "fine"}
    assert report.replaced == {"leak": 4} and not report.unfixable
    # the original is untouched (frozen models), and nothing left to refuse
    assert doc.title != NOTE
    again, second = sweep_model(swept, blocked, NOTE)
    assert again is swept and not second.replaced


def test_the_sweep_never_touches_keys_and_cannot_replace_an_address() -> None:
    doc = Doc(
        title="T1",
        rows=(
            Row(
                ref=f"{OTHER}",  # an identifier field: never repaired
                row_id=f"{OTHER}",
                text="clean",
                url=f"https://{OTHER.split()[0].lower()}.example/{OTHER}",
            ),
        ),
    )
    swept, report = sweep_model(doc, blocked, NOTE)
    assert swept is doc  # nothing it may repair was refused
    assert report.unfixable == {"leak": 1} and not report.replaced


def test_short_strings_are_not_asked() -> None:
    asked: list[str] = []

    def spy(text: str) -> str | None:
        asked.append(text)
        return None

    sweep_model(Doc(title="ab", rows=()), spy, NOTE)
    assert asked == []


def test_a_scrubbed_member_scans_clean_and_counts_what_it_replaced() -> None:
    tool = broker("p_scrape")
    # the search broker's site, read from config so no test spells a vendor name
    site = ".".join(
        (urlsplit(load_providers_config().providers["p_search"].base_url).hostname or "").split(
            "."
        )[-2:]
    )
    text = (
        f"alpha vs {OTHER}; via {tool}; tax credits here; see https://{site}/x and "
        f"https://news.example/ok"
    )
    scrubbed, n = scrub_member(text, (OTHER,), "[withheld]")
    assert n >= 4
    assert "https://news.example/ok" in scrubbed  # an ordinary link is kept
    assert not leak_hits(scrubbed, (OTHER,)) and "credits" not in scrubbed.lower()
    assert site not in scrubbed
    assert scrub_member(scrubbed, (OTHER,), "[withheld]") == (scrubbed, 0)  # idempotent


def test_scrub_text_removes_what_scan_text_finds() -> None:
    text = f"{OTHER} and {broker('p_people')} spent 40 credits"
    assert scan_text(text, (OTHER,))
    scrubbed, n = scrub_text(text, (OTHER,), "[x]")
    assert n >= 2 and not scan_text(scrubbed, (OTHER,))
