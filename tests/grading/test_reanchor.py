"""G4 repair: a supporting sentence that lacks the value is re-anchored to the stored text's own
sentence that carries it (a neighbour, or a close paraphrase), checked exactly as a quoted one;
a value the text never states still drops the claim ``value_not_in_sentence``."""

from __future__ import annotations

import pytest

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.pipeline import REANCHORED
from account_intelligence.grading.spans import reanchor_sentence, split_sentences
from tests.grading.test_lying_generator import dropped, only, run
from tests.grading.world import LEADERSHIP, LEADERSHIP_TEXT, NEWS, sourced

TEXT = (
    "Our leadership. Jane Doe is the Chief Technology Officer of Acme. She joined in 2024. "
    "Acme raised $40 million in Series B funding led by Example Ventures. The round closed in May."
)


def test_sentences_split_on_terminal_punctuation_only() -> None:
    assert split_sentences("Acme uses Salesforce.com daily. It works! Does it? Yes") == [
        "Acme uses Salesforce.com daily.",
        "It works!",
        "Does it?",
        "Yes",
    ]


def test_the_value_one_sentence_over_joins_the_neighbour() -> None:
    cfg = load_grading_config()
    got = reanchor_sentence("Jane Doe", "She joined in 2024.", TEXT, "casefold_text", cfg)
    assert got == "Jane Doe is the Chief Technology Officer of Acme. She joined in 2024."
    got = reanchor_sentence("$40 million", "The round closed in May.", TEXT, "number", cfg)
    assert got == (
        "Acme raised $40 million in Series B funding led by Example Ventures."
        " The round closed in May."
    )


def test_a_paraphrase_is_replaced_by_the_sentence_it_paraphrases() -> None:
    cfg = load_grading_config()
    got = reanchor_sentence(
        "$40 million", "Acme raised 40 million dollars in its Series B round.", TEXT, "number", cfg
    )
    assert got == "Acme raised $40 million in Series B funding led by Example Ventures."


def test_a_value_the_text_never_states_is_not_rescued() -> None:
    cfg = load_grading_config()
    assert reanchor_sentence("$45 million", "The round closed in May.", TEXT, "number", cfg) is None
    assert reanchor_sentence("John Roe", "She joined in 2024.", TEXT, "casefold_text", cfg) is None
    # a sentence with nothing in common with the cited one is not a paraphrase of it
    assert (
        reanchor_sentence(
            "Jane Doe", "Revenue grew strongly last quarter.", TEXT, "casefold_text", cfg
        )
        is None
    )


def test_the_replacement_respects_the_length_bounds() -> None:
    cfg = load_grading_config()
    neighbour = "The team ships code every week and reviews it twice, " * 11 + "always."
    assert len(neighbour) < cfg.spans.max_sentence_chars < len("Jane Doe leads. " + neighbour)
    # the joined window would carry the value but is too long, and the short sentence that
    # carries it shares no word with the cited one: no rescue
    assert (
        reanchor_sentence(
            "Jane Doe", neighbour, "Jane Doe leads. " + neighbour, "casefold_text", cfg
        )
        is None
    )


@pytest.mark.asyncio
async def test_the_engine_keeps_a_claim_whose_value_sits_one_sentence_over() -> None:
    assert LEADERSHIP_TEXT.endswith("She joined in 2024.")
    c = only(
        await run([sourced("c", "exec.cto.name", "Jane Doe", (LEADERSHIP, "She joined in 2024."))])
    )
    assert REANCHORED in c.flags
    assert c.evidence[0].supporting_sentence == (
        "Jane Doe is the Chief Technology Officer of Acme. She joined in 2024."
    )


@pytest.mark.asyncio
async def test_the_engine_still_drops_a_value_the_source_never_states() -> None:
    dropped(
        await run([sourced("c", "exec.cto.name", "John Roe", (LEADERSHIP, "She joined in 2024."))]),
        "value_not_in_sentence",
    )
    dropped(
        await run(
            [
                sourced(
                    "f",
                    "funding.series_b.amount",
                    "$45 million",
                    (NEWS, "Acme raised $40 million in Series B funding led by Example Ventures."),
                )
            ]
        ),
        "value_not_in_sentence",
    )
