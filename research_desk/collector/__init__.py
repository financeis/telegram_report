"""Collector: Telegram channel → PDF files + ``reports`` rows (tagging_status 'pending').

- ``settings``: the collector's settings (``Config``, ``load_config``).
- ``telegram``: the Telethon wrapper and the PDF-message predicates.
- ``storage``: local PDF files + Supabase metadata (``reports``, ``failed_attempts``).
- ``run``: one collection cycle — Stage A retries past failures, Stage B fetches new messages.
- ``cli``: the ``collect`` command; ``register(subparsers)`` adds it to the entry point.

Section numbers in this package's comments refer to the original design notes:
"collector design" = docs/superpowers/specs/2026-05-05-telegram-report-collector-design.md,
"backfill design" = docs/superpowers/specs/2026-05-06-parallel-and-backfill-design.md.
"""
