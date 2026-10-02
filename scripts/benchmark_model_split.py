#!/usr/bin/env python3
"""Summarize supplied real September paired results; this script makes no model calls."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.benchmarks.model_split import ARMS, load_real_benchmark, summarize_arm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    dataset = load_real_benchmark(args.results)
    print("| Arm | Mean coverage | Mean citation validity | Mean cost USD | Mean elapsed seconds |")
    print("|---|---:|---:|---:|---:|")
    for arm in ARMS:
        result = summarize_arm(dataset["accounts"], arm)
        print(
            f"| {arm} | {result.coverage:.3%} | {result.citation_validity:.3%} "
            f"| {result.cost_usd:.4f} | {result.elapsed_seconds_mean:.2f} |"
        )


if __name__ == "__main__":
    main()
