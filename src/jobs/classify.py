"""Conservative deterministic classification proposal engine."""

from jobs.models import Classification, ClassificationLabel


def classify_rules(text: str, rules: list[dict[str, object]]) -> Classification:
    """Apply configured proposal cues; conflicting labels remain unclear."""
    hits: list[tuple[str, str]] = []
    for rule in rules:
        label = str(rule.get("label", "unclear"))
        rule_id = str(rule.get("rule_id", ""))
        cues = rule.get("cues", [])
        if label not in {
            x.value for x in ClassificationLabel if x != ClassificationLabel.UNCLEAR
        } or not isinstance(cues, list):
            continue
        if any(str(cue).casefold() in text.casefold() for cue in cues):
            hits.append((label, rule_id))
    labels = {label for label, _ in hits}
    if len(labels) > 1:
        return Classification(label=ClassificationLabel.UNCLEAR, reason="ambiguous_rules")
    if len(hits) == 1:
        return Classification(
            label=ClassificationLabel(hits[0][0]), reason="rule_match", rule_id=hits[0][1]
        )
    return Classification(label=ClassificationLabel.UNCLEAR, reason="no_rule_match")
