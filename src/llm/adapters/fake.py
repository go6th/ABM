"""A scripted model adapter (tests, replay mode): replies are keyed by task marker and input hash;
each reply is text or an exception instance. An unscripted call raises ``KeyError`` (never an
invented answer)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from llm.config import Candidate
from llm.models import AdapterReply, Usage

Script = Callable[[Candidate, str, str], str | Exception]


class ScriptedAdapter:
    """``ModelAdapter`` driven by a function of (candidate, system, user)."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self,
        candidate: Candidate,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float | None,
        cache_prefix: bool,
    ) -> AdapterReply:
        """Return or raise what the script says."""
        self.calls.append((candidate.name, hashlib.sha256(user.encode()).hexdigest()[:12]))
        reply = self.script(candidate, system, user)
        if isinstance(reply, Exception):
            raise reply
        tokens = max(1, len(user) // 4)
        return AdapterReply(
            reply,
            candidate.model,
            Usage(tokens, max(1, len(reply) // 4), tokens // 2 if cache_prefix else 0, 0),
        )
