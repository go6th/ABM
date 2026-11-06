"""A quote the model altered is re-anchored to the payload's own sentence that carries the value
(G4 kept: the replacement is verbatim payload text); a value the payload never states is not."""

from __future__ import annotations

from agents.guards import evidence_problem, reanchor_quotes
from agents.schemas import EvidenceRef, PayloadView

TEXT = (
    "Acme opened a new plant in Pune. The plant employs 1,200 people and makes pumps.\n"
    "Jane Doe, Chief Operating Officer, said the site doubles capacity."
)
PAYLOAD = PayloadView(
    payload_ref="pl/1", row_key="company_news", url="https://a.example/x", text=TEXT
)
GIVEN = {"pl/1": PAYLOAD}


def test_a_paraphrased_quote_becomes_the_payload_sentence_that_carries_the_value() -> None:
    altered = (EvidenceRef(payload_ref="pl/1", quote="The new plant employs 1200 people"),)
    fixed, n = reanchor_quotes("1,200 people", altered, GIVEN)
    assert n == 1
    assert fixed[0].quote == "The plant employs 1,200 people and makes pumps."
    assert evidence_problem(fixed, GIVEN) is None


def test_two_sentences_run_together_are_anchored_to_the_one_with_the_value() -> None:
    run_on = (
        EvidenceRef(
            payload_ref="pl/1",
            quote="Acme opened a new plant in Pune, Jane Doe Chief Operating Officer said",
        ),
    )
    fixed, n = reanchor_quotes("Jane Doe", run_on, GIVEN)
    assert n == 1 and fixed[0].quote.startswith("Jane Doe, Chief Operating Officer")


def test_a_verbatim_quote_is_left_alone() -> None:
    exact = (EvidenceRef(payload_ref="pl/1", quote="Acme opened a new plant in Pune."),)
    assert reanchor_quotes("Pune", exact, GIVEN) == (exact, 0)


def test_a_value_the_payload_never_states_is_not_rescued() -> None:
    forged = (EvidenceRef(payload_ref="pl/1", quote="The plant employs 1,200 people in Goa"),)
    fixed, n = reanchor_quotes("Goa", forged, GIVEN)
    assert n == 0 and fixed == forged
    assert evidence_problem(fixed, GIVEN) == "quote_not_in_payload"


def test_a_quote_with_nothing_in_common_with_the_sentence_is_not_rescued() -> None:
    unrelated = (EvidenceRef(payload_ref="pl/1", quote="Revenue grew strongly last quarter"),)
    fixed, n = reanchor_quotes("1,200 people", unrelated, GIVEN)
    assert n == 0 and fixed == unrelated


def test_an_unknown_payload_is_left_for_the_guard() -> None:
    missing = (EvidenceRef(payload_ref="pl/9", quote="The plant employs 1200 people"),)
    assert reanchor_quotes("1,200 people", missing, GIVEN) == (missing, 0)
    assert evidence_problem(missing, GIVEN) == "unknown_payload"
