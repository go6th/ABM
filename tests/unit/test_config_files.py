"""P0-5: every configuration file loads through its typed model; the output spec and the
checklist are validated, not trusted. GUARANTEE(test_every_config_file_validates_through_one_loader)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from account_intelligence.config import CONFIG_DIR
from account_intelligence.config_files import load_all, load_output_spec


def test_every_config_file_validates_through_one_loader() -> None:
    hashes = load_all()
    for name in (
        "checklist.yaml",
        "output_spec.yaml",
        "workflow_v1.yaml",
        "tracker_v1.yaml",
        "grading.yaml",
        "providers.yaml",
        "models.yaml",
        "billing.yaml",
    ):
        assert len(hashes[name]) == 64, name
    spec = load_output_spec()
    assert {d.id for d in spec.deliverables} >= {"summary", "evidence", "people", "jobs", "tech"}
    assert "tracker_tally" in {g.id for g in spec.quality_gate}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["deliverables"][0].update(filename="Summary"),  # no {account}
        lambda d: d["deliverables"][0].update(format="pdf"),
        lambda d: d["deliverables"].append(dict(d["deliverables"][0])),  # duplicate id
        lambda d: d["deliverables"][-2].update(includes=["nope"]),
        lambda d: d.update(quality_gate=[]),
        lambda d: d.update(surprise=True),
    ],
)
def test_a_broken_output_spec_is_refused(tmp_path: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    data = yaml.safe_load((CONFIG_DIR / "output_spec.yaml").read_text())
    mutate(data)
    (tmp_path / "output_spec.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        load_output_spec(tmp_path)
