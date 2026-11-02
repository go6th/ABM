"""Places and the answer expanders (Ask redesign 3b, 2026-11-02).

GUARANTEE(test_places_resolve_variants_and_measure_distance)
GUARANTEE(test_aliases_and_nearby_expand_a_filter_without_losing_the_exact_matches)
"""

from __future__ import annotations

import pytest

from documents import expanders as EXP
from documents import places as P


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("San Francisco, CA, USA", "San Francisco, CA, US"),
        ("san francisco", "San Francisco, CA, US"),
        ("SF", "San Francisco, CA, US"),
        ("Greater San Francisco Bay Area", "San Francisco, CA, US"),
        ("Remote - Oakland, CA", "Oakland, CA, US"),
        ("Downtown, San Francisco, CA", "San Francisco, CA, US"),
        ("Oakland, California", "Oakland, CA, US"),
        ("Cambridge, MA", "Cambridge, MA, US"),
        ("Washington, DC", "Washington, DC, US"),
        ("NYC", "New York City, NY, US"),
        ("São Paulo, Brazil", "São Paulo, BR"),
        ("Berlin, Germany", "Berlin, DE"),
    ],
)
def test_places_resolve_variants_and_measure_distance(text: str, label: str) -> None:
    place = P.resolve(text)
    assert place is not None and place.label == label


def test_a_text_without_a_city_has_no_place() -> None:
    for text in ("Remote", "Hybrid", "United States", "", "   ", None):
        assert P.resolve(text) is None


def test_distance_is_the_great_circle_in_miles() -> None:
    sf, oak = P.resolve("San Francisco"), P.resolve("Oakland, CA")
    assert sf and oak
    assert 7 < P.miles(sf, oak) < 10 and P.miles(sf, sf) == 0
    la = P.resolve("Los Angeles")
    assert la and 340 < P.miles(sf, la) < 360


def test_aliases_and_nearby_expand_a_filter_without_losing_the_exact_matches() -> None:
    assert EXP.aliases_of("SFDC") == ["salesforce", "salesforce.com"]
    assert "vp" in EXP.aliases_of("Vice President") and EXP.aliases_of("accountant") == []
    # an alias group never reaches into a longer word
    assert EXP.word_pattern(["vp"]) == r"\m(?:vp)\M"
    assert EXP.is_place_column("location", "Location") and EXP.is_place_column("hq_city", "HQ")
    assert not EXP.is_place_column("title", "Title")
    assert EXP.radius(None) == 50 and EXP.radius(5) == 5 and EXP.radius(10_000) == 300

    rows = [
        {"row": 1, "cells": {"loc": "Oakland, CA"}},
        {"row": 2, "cells": {"loc": "Sacramento, CA"}},  # about 75 miles
        {"row": 3, "cells": {"loc": "Remote"}},
        {"row": 4, "cells": {"loc": "Palo Alto"}},
        {"row": 5, "cells": {"loc": ""}},
        {"row": 6, "cells": {"loc": "SF"}},  # the same place, another spelling
    ]
    found = EXP.nearby("San Francisco", rows, "loc", 50)
    assert found is not None
    assert [r["row"] for r in found["rows"]] == [6, 1, 4]  # nearest first, 0 miles first
    assert found["rows"][0]["distance_miles"] == 0 and found["unplaced"] == 2
    assert found["target"] == "San Francisco, CA, US" and found["radius_miles"] == 50
    assert [r["row"] for r in EXP.nearby("San Francisco", rows, "loc", 100)["rows"]] == [6, 1, 4, 2]  # type: ignore[index]
    assert EXP.nearby("Remote", rows, "loc", 50) is None  # the place asked for is no city
