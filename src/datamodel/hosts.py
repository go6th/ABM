"""Host folding shared by the entity lock (agents) and the pipelines below it (jobs, people):
the lower-case host of a URL, its registrable domain and the brand label of that domain. Pure."""

from __future__ import annotations

from urllib.parse import urlsplit

# second-level registries where the registrable name is the third label from the right
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"})


def host_of(url: str | None) -> str:
    """The lower-case host of ``url`` without a leading ``www.`` ("" when there is none)."""
    if not url:
        return ""
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def registrable(host: str) -> str:
    """The registrable domain of a host: ``careers.vizientinc.com`` is ``vizientinc.com``,
    ``jobs.acme.co.uk`` is ``acme.co.uk``."""
    labels = [p for p in host.lower().removeprefix("www.").split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def brand_label(host: str) -> str:
    """The name part of a registrable domain (``vizent.co`` is ``vizent``)."""
    return registrable(host).split(".", 1)[0]
