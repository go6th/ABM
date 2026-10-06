"""Typed instruments from the catalog (O5). ``Instruments.record(name, value, labels)`` refuses a
metric not in the catalog (``KeyError``: a defect, caught by the contract test), drops any label
not declared for it or outside its closed values (counted), and never raises at runtime for bad
label values.

GUARANTEE(test_instruments_drop_undeclared_labels): O5.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opentelemetry import metrics

from obs.catalog import Catalog
from obs.exporters import DropCounter


class Instruments:
    """Catalog-bound instruments on one meter."""

    def __init__(self, catalog: Catalog, meter: metrics.Meter, drops: DropCounter) -> None:
        self.catalog, self.drops = catalog, drops
        self._inst: dict[str, Any] = {}
        for m in catalog.metrics.values():
            if m.type == "counter":
                self._inst[m.name] = meter.create_counter(m.name, unit=m.unit)
            elif m.type == "histogram":
                self._inst[m.name] = meter.create_histogram(m.name, unit=m.unit)
            elif m.type == "up_down":
                self._inst[m.name] = meter.create_up_down_counter(m.name, unit=m.unit)
            else:
                self._inst[m.name] = meter.create_gauge(m.name, unit=m.unit)

    def labels(self, name: str, labels: Mapping[str, object]) -> dict[str, str]:
        """Declared labels with allowed values; everything else dropped and counted."""
        spec = self.catalog.metrics[name]
        out: dict[str, str] = {}
        for key, value in labels.items():
            ls = self.catalog.label_sets.get(key)
            text = str(value)
            ok = key in spec.labels and ls is not None and len(text) <= 64
            if ok and ls is not None and ls.values is not None and text not in ls.values:
                ok = False
            if ok:
                out[key] = text
            else:
                self.drops.add("metrics")
        return out

    def record(self, name: str, value: float, labels: Mapping[str, object] | None = None) -> None:
        """Add, record or set by the metric's type."""
        spec = self.catalog.metrics[name]
        attrs = self.labels(name, labels or {})
        inst = self._inst[name]
        if spec.type in ("counter", "up_down"):
            inst.add(value, attrs)
        elif spec.type == "histogram":
            inst.record(value, attrs)
        else:
            inst.set(value, attrs)
