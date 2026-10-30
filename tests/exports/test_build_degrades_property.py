"""Property: for any synthetic content that mixes restricted terms, tool words and the cost word
into the texts and links the files print, ``build_all`` returns ``complete`` (never ``failed``),
the offending texts are withheld, at most the Summary is held back (and only when its narrative
carried the text), and none of the planted strings reaches any file. Hypothesis draws which
fields are planted and with what; the account, the other account and every string are synthetic
(spec 9.3)."""

from __future__ import annotations

import asyncio

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from account_intelligence.grading.config import load_grading_config
from agents.naming import assert_client_clean
from agents.production.delivery import member_texts
from exports.builder import ExportBuilder
from exports.config import load_export_config
from tests.exports.corpus import account
from tests.exports.degrade_kit import OTHER_DOMAIN, OTHER_NAME, stored_text
from tests.exports.test_build_degrades_restricted import INJECTIONS, with_terms
from tests.exports.test_build_degrades_third_party import tool_host
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

CFG, GRADING = load_export_config(), load_grading_config()
WORDS = st.sampled_from(
    [
        OTHER_NAME,
        OTHER_NAME.upper(),
        OTHER_DOMAIN,
        f"see https://{tool_host()}/x",
        "R&D tax credits",
        "Parallel Computing",
        "credits",
    ]
)
FILLER = st.text(alphabet=st.characters(whitelist_categories=("L", "N", "Zs")), max_size=12)
SITES = sorted(set(INJECTIONS) - {"lookalike"})  # the lookalike's term is a title of its own
SETTINGS = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
# a planted string that must reach no file (lower case)
PLANTED = (OTHER_NAME.casefold(), OTHER_DOMAIN, "credits", tool_host())


@SETTINGS
@given(
    st.lists(st.sampled_from(SITES), min_size=1, max_size=4, unique=True),
    WORDS,
    FILLER,
    FILLER,
)
def test_planted_terms_never_end_the_research(
    sites: list[str], word: str, before: str, after: str
) -> None:
    c = with_terms(account(), OTHER_NAME, OTHER_DOMAIN)
    for site in sites:
        # the same injection the examples use, with the drawn word in place of the fixed one
        c = INJECTIONS[site](c)
        c = c.model_copy(update={"forbidden_terms": (OTHER_NAME, OTHER_DOMAIN)})
    c = _replace_word(c, f"{before}{word}{after}")
    store = MemoryStore()
    builder = ExportBuilder(FakeReader(c), store, InProcessRenderer(), CFG, GRADING)
    result = asyncio.run(builder.build_all(c.tenant_id, c.run_id))
    assert result.status == "complete", (sites, word, result.errors)
    assert set(result.withheld_files) <= {"summary:missing_narrative"}, result.withheld_files
    text = stored_text(store, result).casefold()
    for planted in PLANTED:
        assert planted not in text, (sites, word, planted)
    for d in result.deliverables:
        assert_client_clean(member_texts(store.objects[d.object_key]))


def _replace_word(c, word: str):  # type: ignore[no-untyped-def]
    """The planted name of the injections swapped for the drawn word, everywhere it was put."""
    raw = c.model_dump_json().replace(OTHER_NAME, word.replace('"', "'").replace("\\", " "))
    return type(c).model_validate_json(raw)
