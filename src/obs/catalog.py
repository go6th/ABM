"""Load and validate the telemetry catalogs (pure; D-56, O5, O14).

* metrics: every metric has a type, a unit, labels from ``label_sets`` (closed ``values`` or a
  ``bound``), a ``series_bound`` at least the product of its labels' cardinalities, and an owner;
  forbidden label names (tenant, account, run, person, user, domain, URL, email, name, text...)
  fail validation;
* spans: names and their attribute allow-lists (forbidden names also refused);
* logs: the field allow-list and the value length cap;
* the catalogs are hashed; the hash is a resource attribute (O16).

GUARANTEE(test_catalogs_validate_and_refuse_forbidden_labels): O5.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from obs.errors import CatalogInvalid

FORBIDDEN = frozenset(
    {
        "tenant",
        "tenant_id",
        "account",
        "account_id",
        "run",
        "run_id",
        "person",
        "person_id",
        "user",
        "user_id",
        "domain",
        "url",
        "email",
        "name",
        "company",
        "query",
        "text",
        "prompt",
        "title",
        "value",
        "sentence",
    }
)
TYPES = frozenset({"counter", "histogram", "gauge", "up_down"})


@dataclass(frozen=True)
class LabelSet:
    """A label's closed values (or only its bound)."""

    name: str
    values: frozenset[str] | None
    bound: int


@dataclass(frozen=True)
class Metric:
    """One catalog metric."""

    name: str
    type: str
    unit: str
    labels: tuple[str, ...]
    series_bound: int
    owner: str


@dataclass(frozen=True)
class Catalog:
    """All three catalogs, validated."""

    label_sets: Mapping[str, LabelSet]
    metrics: Mapping[str, Metric]
    spans: Mapping[str, frozenset[str]]
    log_fields: frozenset[str]
    max_value_chars: int
    content_hash: str


def _yaml(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or ("status" not in data and "source" not in data):
        raise CatalogInvalid(f"{path.name} must be a mapping with a source or status")
    return data, raw


def build(
    metrics_doc: Mapping[str, Any],
    spans_doc: Mapping[str, Any],
    logs_doc: Mapping[str, Any],
    digest: str,
) -> Catalog:
    """Validate the three documents (``CatalogInvalid`` on anything wrong)."""
    try:
        sets: dict[str, LabelSet] = {}
        for name, spec in metrics_doc["label_sets"].items():
            if name in FORBIDDEN:
                raise CatalogInvalid(f"label {name} is forbidden")
            if "source" not in spec and "status" not in spec:
                raise CatalogInvalid(f"label set {name} has no source or status")
            values = spec.get("values")
            if values is not None:
                sets[name] = LabelSet(name, frozenset(str(v) for v in values), len(values))
            else:
                bound = int(spec["bound"])
                if bound < 1:
                    raise CatalogInvalid(f"label set {name} needs a positive bound")
                sets[name] = LabelSet(name, None, bound)
        metrics: dict[str, Metric] = {}
        for name, spec in metrics_doc["metrics"].items():
            if spec["type"] not in TYPES:
                raise CatalogInvalid(f"metric {name} has an unknown type")
            labels = tuple(str(x) for x in spec["labels"])
            for label in labels:
                if label in FORBIDDEN:
                    raise CatalogInvalid(f"metric {name} uses forbidden label {label}")
                if label not in sets:
                    raise CatalogInvalid(f"metric {name} uses undeclared label {label}")
            series = math.prod(sets[x].bound for x in labels) if labels else 1
            bound = int(spec["series_bound"])
            if series > bound:
                raise CatalogInvalid(f"metric {name}: {series} series exceed its bound {bound}")
            if not spec.get("owner"):
                raise CatalogInvalid(f"metric {name} has no owner")
            metrics[str(name)] = Metric(
                str(name), str(spec["type"]), str(spec["unit"]), labels, bound, str(spec["owner"])
            )
        spans: dict[str, frozenset[str]] = {}
        for name, spec in spans_doc["spans"].items():
            attrs = frozenset(str(a) for a in spec["attributes"])
            bad = attrs & FORBIDDEN
            if bad:
                raise CatalogInvalid(f"span {name} carries forbidden attributes {sorted(bad)}")
            spans[str(name)] = attrs
        fields = frozenset(str(f) for f in logs_doc["fields"])
        if fields & {"prompt", "text", "sentence", "url", "email", "name", "value"}:
            raise CatalogInvalid("log fields may not carry content")
        cap = int(logs_doc["max_value_chars"]["value"])
        return Catalog(sets, metrics, spans, fields, cap, digest)
    except CatalogInvalid:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CatalogInvalid(f"invalid telemetry catalog: {exc!r}") from exc


def config_dir() -> Path:
    """``config/telemetry``."""
    return Path(__file__).resolve().parents[2] / "config" / "telemetry"


@lru_cache(maxsize=4)
def load_catalog(directory: Path | None = None) -> Catalog:
    """Load, validate and hash the catalogs."""
    d = directory or config_dir()
    docs = [_yaml(d / f) for f in ("metrics.yaml", "spans.yaml", "logs.yaml")]
    digest = hashlib.sha256(b"".join(raw for _, raw in docs)).hexdigest()
    return build(docs[0][0], docs[1][0], docs[2][0], digest)
