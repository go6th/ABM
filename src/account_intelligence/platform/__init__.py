"""Platform ports and their adapters: hermetic (tests, dev) and Google Cloud (staging, prod).

Every port here has ONE contract suite (``tests/system/test_port_contracts.py``) that both
adapters pass. ``account_intelligence.app.container`` is the only place that picks an adapter.
"""
