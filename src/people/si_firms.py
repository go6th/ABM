"""Former systems-integrator and consulting employers of a person (the People List column
"Former SI and consulting employers (firm and years)"). Pure.

A profile's experience entries are matched, by employer name, to the configured firm list
(``si_firms`` in ``config/people_rubric.yaml``: whole-word, case-insensitive regular-expression
aliases). Each matching stint prints as ``Firm (2015 to 2019)``; a stint still running prints
``Firm (2020 to date)``; a stint with no start year prints the firm alone. The entries keep the
profile's own order. Without dated entries the profile's plain prior employer names are matched
and print the firm alone. ``None`` means no profile data at all (the cell stays empty: nothing
was read, so nothing is claimed); an empty tuple means a profile was read and no listed firm
appears in it.
"""

from __future__ import annotations

from collections.abc import Sequence

from people.config import SiFirm
from people.cues import clean, fragment_pattern
from people.models import Employment, Profile


def firm_of(employer: str, firms: Sequence[SiFirm]) -> str | None:
    """The configured firm named by ``employer``, or ``None``."""
    text = clean(employer, 300)
    for firm in firms:
        if any(fragment_pattern(alias).search(text) for alias in firm.aliases):
            return firm.firm
    return None


def _stint(firm: str, employment: Employment) -> str:
    if employment.start is None:
        return firm
    end = str(employment.end.year) if employment.end is not None else "date"
    return f"{firm} ({employment.start.year} to {end})"


def former_si_employers(profile: Profile | None, firms: Sequence[SiFirm]) -> tuple[str, ...] | None:
    """The printed entries for one person (see the module docstring)."""
    if profile is None:
        return None
    if profile.employments is not None:
        out: list[str] = []
        for employment in profile.employments:
            firm = firm_of(employment.employer, firms)
            if firm is not None:
                out.append(_stint(firm, employment))
        return tuple(dict.fromkeys(out))
    if profile.prior_employers is not None:
        names = (firm_of(e, firms) for e in profile.prior_employers)
        return tuple(dict.fromkeys(n for n in names if n is not None))
    return None
