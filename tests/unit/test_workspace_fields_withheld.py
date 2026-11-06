"""The Account workspace screen withholds one refused text, never the whole screen: the 6th
test account's own workspace answered 422 "Response held back" for a provider address in one
field (staging 2026-11-04), the way a sheet cell is withheld and the row kept."""

from __future__ import annotations

from urllib.parse import urlsplit

from account_intelligence.api.routes.account_workspace import _withhold_leaks
from account_intelligence.connectors.settings import load_providers_config


def test_one_refused_field_is_blanked_and_listed_not_the_screen() -> None:
    host = urlsplit(next(iter(load_providers_config().providers.values())).base_url).hostname
    held: list[str] = []
    screen = {
        "account": {"name": "6th", "domain": "6th.ai"},
        "changes": [{"text": "A new posting"}, {"text": f"Pulled from https://{host}/x"}],
        "counts": {"people": 3},
    }
    got = _withhold_leaks(screen, held)
    assert held == ["changes.text"]
    assert got["account"] == {"name": "6th", "domain": "6th.ai"}
    assert got["counts"] == {"people": 3}
    assert got["changes"] == [{"text": "A new posting"}, {"text": ""}]
