"""Lane inference from platform names (code, never the model).

The job pipeline's lexicon (``config/job_lanes.yaml``: platform names and aliases per lane, from
the owner's skill) is the one list of platform terms. A finding whose quoted sentence or value
names a platform belongs to that platform's lane; a sentence naming platforms of two lanes
belongs to none (the seam itself is the finding, and the model may still name a lane). The
skill's standing rule holds here too: ServiceMax, Siebel and Databricks are not in the lexicon.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from jobs.match import match_platforms


@lru_cache(maxsize=1)
def _lane_of_platform() -> dict[str, str]:
    from jobs.config import load_jobs_config

    cfg = load_jobs_config()
    return {name: cfg.lane_of(name) for name in cfg.lexicon}


@lru_cache(maxsize=1)
def _lexicon() -> dict[str, tuple[str, ...]]:
    from jobs.config import load_jobs_config

    return dict(load_jobs_config().lexicon)


def platforms_named(text: str) -> tuple[str, ...]:
    """The canonical platform names the text names (whole words, any case)."""
    found = match_platforms("", text or "", _lexicon())
    return tuple(found.description)


def infer_lane(texts: Iterable[str], allowed: Iterable[str]) -> str | None:
    """The one lane whose platforms the texts name, when it is an allowed lane; None when the
    texts name no platform, name platforms of two lanes, or name a lane the run does not use."""
    lanes = {_lane_of_platform()[p] for t in texts for p in platforms_named(t)}
    if len(lanes) != 1:
        return None
    lane = next(iter(lanes))
    return lane if lane in set(allowed) else None
