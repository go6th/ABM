"""Nothing paid starts without a person saying yes twice (owner's rule 2026-11-05).

On 2026-11-05 an uploaded file started a paid model run by itself (``app.auto_analysis``) and
cost the owner about 100 USD before anyone saw it. The rule since then:

* every request that starts research (new research, refresh, resume, a run of the API, a file
  run) carries two separate confirmations, ``confirm`` and ``confirm_again``, both true; the
  screens set the second only after a second "Are you sure?" click. A request without both is
  refused with 428 ``confirm_twice`` before anything is reserved or counted;
* nothing starts a research by itself: the upload's model analysis, the sweep that continues a
  stalled research and the scheduled refresh tick are all off. ``RESEARCH_AUTOMATIC_STARTS=on``
  turns them back on (one switch for all three, off unless set).

GUARANTEE(test_a_start_without_both_confirmations_is_refused)
GUARANTEE(test_automatic_starts_are_off_by_default)
"""

from __future__ import annotations

import os
from collections.abc import Mapping

AUTOMATIC_STARTS_ENV = "RESEARCH_AUTOMATIC_STARTS"
CONFIRM_TWICE_CODE = "confirm_twice"
CONFIRM_TWICE_TITLE = "Confirm twice to start"
CONFIRM_TWICE_TEXT = (
    "Research spends real money. Confirm it, then confirm it again, before it starts."
)


def automatic_starts_allowed(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``RESEARCH_AUTOMATIC_STARTS`` is ``on``: an upload, a stalled run or a
    schedule may then start paid research without a person. Off when unset."""
    value = (env if env is not None else os.environ).get(AUTOMATIC_STARTS_ENV, "off")
    return value.strip().lower() == "on"


def confirmed_twice(confirm: bool | None, confirm_again: bool | None) -> bool:
    """Both confirmations of a start request are present and true."""
    return confirm is True and confirm_again is True
