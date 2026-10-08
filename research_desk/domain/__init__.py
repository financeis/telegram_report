"""Shared rules and reference data that every part must read the same way.

- ``reports``: report vocabulary (vocabulary.yaml), the in-scope rule, the out-of-scope row shape.
- ``stocks``: the stock list (KRX CSV) loader, lookups, search, catalog, and its version file.

No DB access and no web routes here. File paths come in as arguments.
"""
