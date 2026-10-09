"""Review (검토): the manual review of reports the tagger could not settle (``review_needed``).

Review owns its reads and writes of the reports table (spec §4): the review queue, the review
decisions (verify / out of scope / retag) and their undo (spec §9.8). Public:

- ``router``: ``GET /api/review``, ``GET /api/review/{rid}/pdf``, ``GET /api/review/{rid}/preview``,
  ``GET /api/review/{rid}/pages/{page}``, ``POST /api/review/{rid}/action``,
  ``POST /api/review/undo/{token}`` (spec §6).

A successful action or undo clears the coverage cache through the coverage window
(``coverage.invalidate()``, spec §9.7). Without DB settings the addresses that need the DB answer
``NotReady("검토", …)`` (spec §9.9).
"""
from .router import router
