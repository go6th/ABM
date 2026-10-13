"""The deterministic fake model (hermetic only): the ``LlmPort`` the tests and the hermetic local
stack use when no model provider may be called.

Each answer is a pure function of ``(tier, prompt kind, inputs, scenario)``: no clock, no
randomness, no network. The prompt kind is read from the agent header of an ABM agent prompt
(``<!-- agent: pass1; ... -->``) or from the JSON shape of a Workflow v1 call (planner rows, lead
queries, extractor records, posting labels, support judge, source re-read). Answers are built
only from the inputs (a quote is always copied from a payload it was given) plus the scenario's
canned texts, which live in fixture files and are validated against the live output schemas by
``tests/unit/test_fake_model.py``.

A scenario can make the model misbehave on purpose so the platform's guards are exercised:
``obey_injection`` follows instructions planted in a source page (an injected fact and an
injected tool call); ``judge_contradicts`` makes the judge and the source re-read deny claims
whose value contains one of the listed strings.

Selection: ``app.container.build_container`` wires it only for ``APP_ENV`` ``dev`` or ``test``
with ``LLM_FAKE=hermetic``; ``assert_gcp_runtime`` refuses it in staging and production (W4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from account_intelligence.platform.ports import LlmResult, LlmUsage

_AGENT = re.compile(r"<!-- agent: ([a-z_0-9]+);")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]")
_PERSON = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+) is the ([A-Z][^.]{1,120})\.")
_PASSAGES_MARK = "Passages (untrusted data):"
_DOCUMENT = re.compile(r'<document id="(P\d+)"[^>]*>\n(.*?)\n</document>', re.S)
_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("next_steps", re.compile(r"(?i)\b(next step|follow up|will send|agreed to)\b")),
    ("objections", re.compile(r"(?i)\b(concern|objection|worried|blocker|risk)\b")),
    ("decisions", re.compile(r"(?i)\b(decided|decision|approved|signed off)\b")),
    ("budget_cycles", re.compile(r"(?i)\b(budget|fiscal year|renewal|contract ends)\b")),
    ("incumbent_partners", re.compile(r"(?i)\b(partner|reseller|consultancy|incumbent)\b")),
    ("programs", re.compile(r"(?i)\b(program|programme|initiative|project)\b")),
    ("platforms", re.compile(r"(?i)\b(uses|runs on|migrating to|replace|evaluating)\b")),
)


@dataclass(frozen=True)
class FakeScenario:
    """Canned texts and misbehaviour switches for one scenario (loaded from a fixture file)."""

    name: str
    narrative: tuple[str, ...]
    hypotheses: tuple[str, ...]
    questions: tuple[str, ...]
    obey_injection: bool = False
    injection_trigger: str = ""
    injected_value: str = ""
    injected_query: str = ""
    judge_contradicts: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FakeScenario:
        """Build from a fixture mapping (unknown keys refused, so a typo cannot pass silently)."""
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown fake model scenario keys: {sorted(unknown)}")
        tuples = {"narrative", "hypotheses", "questions", "judge_contradicts"}
        values: dict[str, Any] = {k: tuple(v) if k in tuples else v for k, v in data.items()}
        return cls(**values)


def load_scenario(path: str | Path) -> FakeScenario:
    """A scenario from its JSON fixture."""
    return FakeScenario.from_dict(json.loads(Path(path).read_text()))


def first_sentence(text: str) -> str:
    """The first sentence of ``text`` (the whole text when it has no sentence end)."""
    match = _SENTENCE.search(text)
    return (match.group() if match else text).strip()


def _sentences(text: str) -> list[str]:
    return [m.group().strip() for m in _SENTENCE.finditer(text)]


@dataclass
class FakeModel:
    """``LlmPort`` that answers from the scenario; ``calls`` records ``(tier, kind)`` in order."""

    scenario: FakeScenario
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        """Answer one call (see the module docstring)."""
        kind, text = self.answer(tier, system, user)
        self.calls.append((tier, kind))
        return LlmResult(text, f"fake-{tier}", LlmUsage(len(user) // 4, len(text) // 4))

    def answer(self, tier: str, system: str, user: str) -> tuple[str, str]:
        """``(prompt kind, answer text)``: the pure core."""
        if _PASSAGES_MARK in user:
            return "file_extract", json.dumps({"facts": self._documents(user)})
        data = json.loads(user.split("\nYour previous", 1)[0])
        match = _AGENT.search(system)
        if match:
            agent = match.group(1)
            return agent, json.dumps(self._agent(agent, data))
        kind, out = self._workflow(tier, data)
        return kind, json.dumps(out)

    # Uploaded documents (input mode "file") ------------------------------------------------------

    def _documents(self, user: str) -> list[dict[str, Any]]:
        """Facts of the passages in a document extraction prompt: one per sentence that names a
        category's cue, quoted verbatim; people from "Name Surname is the Role." sentences. With
        ``obey_injection`` an instruction planted in a passage is followed: an invented fact
        whose quote is not in the passage and an item of an unknown kind (both must be dropped
        by the code that checks the reply)."""
        facts: list[dict[str, Any]] = []
        for pid, body in _DOCUMENT.findall(user):
            text = body.replace("&lt;", "<")
            for sentence in _sentences(text):
                person = _PERSON.search(sentence)
                if person:
                    facts.append(
                        {
                            "passage": pid,
                            "category": "people",
                            "attribute": "contact",
                            "value": f"{person.group(1)}, {person.group(2)}",
                            "quote": sentence,
                            "person_name": person.group(1),
                            "person_role": person.group(2),
                        }
                    )
                    continue
                for category, cue in _CUES:
                    if cue.search(sentence):
                        facts.append(
                            {
                                "passage": pid,
                                "category": category,
                                "attribute": category.replace("_", " "),
                                "value": sentence[:480],
                                "quote": sentence[:580],
                            }
                        )
                        break
            if self.scenario.obey_injection and "ignore previous instructions" in text.lower():
                facts.append(
                    {
                        "passage": pid,
                        "category": "decisions",
                        "attribute": "status",
                        "value": "Every research area is done",
                        "quote": "All tracker rows are complete and approved.",
                    }
                )
                facts.append({"passage": pid, "category": "send_email", "to": "ceo@example.com"})
        return facts

    # ABM agents ----------------------------------------------------------------------------------

    def _agent(self, agent: str, data: dict[str, Any]) -> dict[str, Any]:
        if agent == "setup":
            page = next((p for p in data.get("visited_pages", []) if "careers" in p["url"]), None)
            return {"careers_page_id": page["page_id"] if page else None}
        if agent in ("pass1", "pass2"):
            return {"candidates": self._candidates(data.get("payloads", []))}
        if agent == "people":
            return {"people": self._people(data.get("payloads", []))}
        if agent == "sense_check":
            return {"findings": [], "proposals": []}
        if agent == "distill":
            return self._distill(data.get("facts", []))
        return {}

    def _triggered(self, text: str) -> bool:
        trigger = self.scenario.injection_trigger
        return bool(trigger) and trigger.lower() in text.lower()

    def _candidates(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in payloads:
            usable = [s for s in _sentences(p["text"]) if len(s) >= 8 and not self._triggered(s)]
            if usable:
                sentence = usable[0]
                out.append(
                    {
                        "row_key": p["row_key"],
                        "attribute": "signal",
                        "value": " ".join(sentence.split()[:3]),
                        "origin": "read",
                        "evidence": [{"payload_ref": p["payload_ref"], "quote": sentence}],
                    }
                )
            if self.scenario.obey_injection and self._triggered(p["text"]):
                planted = next(s for s in _sentences(p["text"]) if self._triggered(s))
                out.append(
                    {
                        "row_key": p["row_key"],
                        "attribute": "funding.injected",
                        "value": self.scenario.injected_value,
                        "origin": "read",
                        "evidence": [{"payload_ref": p["payload_ref"], "quote": planted[:600]}],
                    }
                )
        return out

    def _people(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in payloads:
            for m in _PERSON.finditer(p["text"]):
                out.append(
                    {
                        "name": m.group(1),
                        "title": m.group(2),
                        "priority": "A",
                        "evidence": [{"payload_ref": p["payload_ref"], "quote": m.group()}],
                    }
                )
        return out

    def _distill(self, facts: list[dict[str, Any]]) -> dict[str, Any]:
        if not facts:
            return {"hypotheses": [], "questions": [], "narrative": []}
        ids = [f["fact_id"] for f in facts]

        def claims(texts: tuple[str, ...]) -> list[dict[str, Any]]:
            return [{"text": t, "fact_ids": [ids[i % len(ids)]]} for i, t in enumerate(texts)]

        return {
            "hypotheses": claims(self.scenario.hypotheses),
            "questions": claims(self.scenario.questions),
            "narrative": claims(self.scenario.narrative),
        }

    # Workflow v1 ---------------------------------------------------------------------------------

    def _workflow(self, tier: str, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if tier == "planner" and "rows" in data:
            return "plan_gaps", self._plan(data)
        if tier == "planner" and "lead" in data:
            return "lead_queries", {
                "calls": [
                    {"operation": "web_search", "args": {"queries": [f"{data['lead']} {suffix}"]}}
                    for suffix in ("announcement", "press coverage")
                ]
            }
        if tier == "extractor" and "records" in data:
            return "extract_facts", {"claims": self._claims(data)}
        if "posting_id" in data:
            return "classify_posting", {
                "posting_id": data["posting_id"],
                "label": "build",
                "evidence_sentence": first_sentence(data["text"]),
            }
        if tier == "judge" and "claim" in data:
            supported = not self._contradicted(str(data["claim"]))
            reason = "the sentence states it" if supported else "the sentence does not state it"
            return "support_judge", {"supported": supported, "reason": reason}
        if tier == "judge" and "question" in data:
            return "source_reread", self._reread(data)
        if "person_key" in data:
            return "classify_person", {"person_key": data["person_key"], "findings": []}
        return "unknown", {}

    def _plan(self, data: dict[str, Any]) -> dict[str, Any]:
        ops = data["operations"]
        calls: list[dict[str, Any]] = []
        domain = str(data.get("account_domain", ""))
        for row in data["rows"]:
            if "web_search" in ops:
                query = f"{domain} {row['label'][:60]}".strip()
                calls.append(
                    {"row": row["key"], "operation": "web_search", "args": {"queries": [query]}}
                )
            elif "tech_detections" in ops and domain:
                calls.append(
                    {"row": row["key"], "operation": "tech_detections", "args": {"domain": domain}}
                )
        if self.scenario.obey_injection and self.scenario.injected_query and data["rows"]:
            # the compromised planner tries the call the planted page asked for
            calls.insert(
                0,
                {
                    "row": data["rows"][0]["key"],
                    "operation": "fetch_url",
                    "args": {"url": self.scenario.injected_query},
                },
            )
        return {"calls": calls[: int(data.get("call_limit", len(calls)))]}

    def _claims(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, record in enumerate(data["records"][:2]):
            usable = [s for s in _sentences(record["text"]) if not self._triggered(s)]
            if not usable:
                continue
            sentence = usable[0]
            out.append(
                {
                    "claim_kind": "sourced",
                    "local_id": f"c{i}",
                    "attribute_key": f"{data['attribute_prefix']}.fact_{i}",
                    "value": " ".join(sentence.split()[:3]),
                    "evidence": [{"url": record["url"], "supporting_sentence": sentence}],
                }
            )
            if self.scenario.obey_injection and self._triggered(record["text"]):
                planted = next(s for s in _sentences(record["text"]) if self._triggered(s))
                out.append(
                    {
                        "claim_kind": "sourced",
                        "local_id": f"injected{i}",
                        # a compromised model picks a key the engine accepts, to sneak it in
                        "attribute_key": "funding.injected",
                        "value": self.scenario.injected_value,
                        "evidence": [{"url": record["url"], "supporting_sentence": planted}],
                    }
                )
        return out

    def _contradicted(self, value: str) -> bool:
        return any(c and c.lower() in value.lower() for c in self.scenario.judge_contradicts)

    def _reread(self, data: dict[str, Any]) -> dict[str, Any]:
        source = str(data.get("source") or "")
        sentence = first_sentence(source) if source else ""
        answer = " ".join(sentence.split()[:3]) if sentence else None
        if answer is None or self._contradicted(answer) or self._contradicted(sentence):
            return {"answer": None, "quote": None}
        return {"answer": answer, "quote": sentence}
