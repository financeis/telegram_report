"""Review (검토): the manual review of reports the tagger could not settle (``review_needed``).

Review owns its reads and writes of the reports table (spec §4): the review queue, the review
decisions (verify / out of scope / retag) and their undo (spec §9.8).
"""
