"""Generate tracker transitions doc."""

from pathlib import Path

import yaml


def main():
    with open("config/tracker_v1.yaml") as f:
        data = yaml.safe_load(f)

    transitions = data["transitions"]

    md = "# Tracker Transition Table\n\n"
    md += "| From \\ To | done | partial | not_done | blocked | none |\n"
    md += "| --- | --- | --- | --- | --- | --- |\n"

    states = ["unset", "not_done", "blocked", "partial", "none", "done"]
    targets = ["done", "partial", "not_done", "blocked", "none"]

    for state in states:
        row = f"| {state} |"
        for t in targets:
            allowed = transitions.get(state, {}).get(t, [])
            row += f" {', '.join(allowed) if allowed else 'no'} |"
        md += row + "\n"

    Path("docs/tracker/transitions.md").write_text(md)


if __name__ == "__main__":
    main()
