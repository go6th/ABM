"""Retrieval helpers the documents package uses: ``models`` (the retrieved-chunk vocabulary),
``retrieval`` (hybrid ranking, reciprocal rank fusion, typed filters) and ``tokenizer`` (the one
shared tokenizer and per-file budget calibration).

The agentic question-answering loop that used to live here (graph, prompt, reflection, cache,
access, guardrails, graphrag, metrics, obs, synthetic, config and ``config/rag.yaml``) had no
importer in ``src/`` and was parked in ``unwanted-things/dead-code-2026-11/`` (ADR-0136).
"""
