"""Which postings a gap pass may re-process (spec 7). Pure.

The remainder is every analysed, non-hostile posting that the first pass left undone for a reason
a later pass can remove:

* unread and matched because the run-level page-read cap skipped its escalation
  (``escalation == "cap"``); a posting whose one read was spent (``failed``) is final;
* unclassified with no verdict because the extractor cap was reached (``cap``) or its processing
  failed and was isolated (``error``).

Verdicts are final: ``disagreement``, an invalid or unverified answer, ``unclear`` and ``hostile``
are never retried (a re-ask would let a lying model try again). Out-of-window postings are never
processed. The order is the posting key, so a pass is deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence

from jobs.models import Posting

RETRYABLE_REASONS = frozenset({"cap", "error"})


def is_remainder(posting: Posting) -> bool:
    """True when a gap pass may re-process this posting (see the module docstring). A posting
    carried from the previous delivered research (``new_since_previous`` False) is never
    re-processed: its reads and classes were paid for by that research."""
    if posting.window_bucket == "out_of_window" or posting.hostile:
        return False
    if posting.new_since_previous is False:
        return False
    verdict = posting.classification
    if verdict is not None and verdict.classified_by == "none":
        return verdict.reason in RETRYABLE_REASONS
    return posting.matched and not posting.read and posting.escalation == "cap"


def gap_remainder(postings: Sequence[Posting]) -> tuple[str, ...]:
    """The posting keys a gap pass may re-process, sorted."""
    return tuple(sorted(p.posting_key for p in postings if is_remainder(p)))


def progress(before: Sequence[Posting], after: Sequence[Posting]) -> bool:
    """True when the pass read or classified at least one more posting."""

    def score(rows: Sequence[Posting]) -> tuple[int, int]:
        read = sum(p.read for p in rows)
        decided = sum(p.read and p.posting_class != "unclassified" for p in rows)
        return read, decided

    old, new = score(before), score(after)
    return new[0] > old[0] or new[1] > old[1]
