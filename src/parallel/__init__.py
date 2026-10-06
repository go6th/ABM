"""Parallel execution v1: a dependency-aware unit scheduler with per-provider concurrency limits.

Pure modules (``dag``, ``units``, ``scheduler`` core, ``merge``, ``critical_path``) take no clock,
no randomness and no I/O. ``governor`` (Postgres leases), ``executor`` and ``simulate`` are the
shells. No model is used anywhere in this package (a static test enforces it).
"""
