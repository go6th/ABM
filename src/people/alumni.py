"""Employer normalisation and reference-list matching for the alumni count (D-22). Pure.

An alumnus is an in-scope person at the account whose prior employers include an employer on the
run's reference list (the pursuing client and any employers the operator names, from run input
only). Employers compare in the name normal form (``people.names``); configured aliases map to
their canonical name. No suffix list (Inc, Ltd and so on) is applied: none is in the sources.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from people.names import normalise_name


def reference_set(employers: Sequence[str], aliases: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """Normal forms of the reference employers and of their configured aliases."""
    names = {normalise_name(e) for e in employers}
    for canonical, alias_list in aliases.items():
        if normalise_name(canonical) in names:
            names.update(normalise_name(a) for a in alias_list)
    return frozenset(n for n in names if n)


def matched_employer(prior_employers: Sequence[str], references: frozenset[str]) -> str | None:
    """The first prior employer (as written) on the reference list, or ``None``."""
    for employer in prior_employers:
        if normalise_name(employer) in references:
            return employer
    return None
