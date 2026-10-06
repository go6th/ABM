"""Command line for CI (D-50):

* ``python -m evalgate.cli gate --candidate C.json [--baseline B.json] --trigger T
  --modes replay=ran,live=not_run --out report.md``: exit 0 on pass, 1 on block (no override);
* ``python -m evalgate.cli contamination --labels DIR --scan PATH...``: exit 1 on any hit;
* ``python -m evalgate.cli bench --id ID --out FILE``: write the Blocked benchmark template.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from evalgate import bench, contamination, gates, golden, report
from evalgate.baseline import from_json
from evalgate.config import load_eval_config
from evalgate.runner import paired_scores


def main(argv: list[str] | None = None) -> int:
    """Run a subcommand; returns the exit code."""
    parser = argparse.ArgumentParser(prog="evalgate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate")
    g.add_argument("--candidate", type=Path, required=True)
    g.add_argument("--baseline", type=Path)
    g.add_argument("--trigger", default="pull_request")
    g.add_argument("--modes", default="")
    g.add_argument("--out", type=Path)
    c = sub.add_parser("contamination")
    c.add_argument("--labels", type=Path, required=True)
    c.add_argument("--scan", type=Path, nargs="+", required=True)
    b = sub.add_parser("bench")
    b.add_argument("--id", required=True)
    b.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cfg = load_eval_config()
    if args.cmd == "gate":
        cand, prov = from_json(json.loads(args.candidate.read_text()))
        base = from_json(json.loads(args.baseline.read_text()))[0] if args.baseline else None
        modes = dict(kv.split("=", 1) for kv in args.modes.split(",") if "=" in kv)
        paired = {m: paired_scores(base, cand, m) for m in cfg.primary_metrics} if base else {}
        verdict = gates.evaluate(
            cfg, args.trigger, modes, cand.metrics, base.metrics if base else None, paired
        )
        text = report.markdown(cand, verdict, prov, cfg.alpha)
        if args.out:
            args.out.write_text(text)
        print(text)
        return 0 if verdict.status == "pass" else 1
    if args.cmd == "contamination":
        gs = golden.load(sorted(args.labels.glob("*.yaml")), date.today(), 10_000)
        values = [lb.value for _, lb in gs.labels()]
        files = [
            p
            for root in args.scan
            for p in (root.rglob("*") if root.is_dir() else [root])
            if p.is_file() and p.suffix in (".md", ".txt", ".json", ".yaml", ".py")
        ]
        hits = contamination.scan(files, values)
        for f, n in hits:
            print(f"contamination: {f}: {n[:30]}")
        return 1 if hits else 0
    args.out.write_text(
        bench.template(
            args.id, [], "needs analyst-labelled golden accounts, real model access and credits"
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
