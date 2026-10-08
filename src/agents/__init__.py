"""ABM agent layer: the skill's eight-step run as bounded agents inside the research workflow.

This package decides which agent runs when, what each may see and call, and what must be true
before the next step (guards G1 to G17). It never grades, tracks, prices or renders: those stay
with the grading engine, the tracker, the ledger and credit gate, and the exporters.
"""
