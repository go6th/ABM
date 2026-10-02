"""One bounded full-text escalation per posting."""

from jobs.models import RawPosting
from jobs.ports import PageReadPort


async def escalate_once(
    page_reader: PageReadPort, posting: RawPosting, *, max_chars: int
) -> RawPosting | None:
    """Perform one URL read only when completeness is unknown or false."""
    if posting.text_complete is True:
        return posting
    if not posting.url:
        return None
    return await page_reader.read(posting.url, max_chars=max_chars)
