"""Collector: Telegram channel → PDF files + ``reports`` rows (tagging_status 'pending').

- ``settings``: the collector's settings (``Config``, ``load_config``).
- ``telegram``: the Telethon wrapper and the PDF-message predicates.
- ``storage``: local PDF files + Supabase metadata (``reports``, ``failed_attempts``).
- ``run``: one collection cycle — Stage A retries past failures, Stage B fetches new messages.
- ``cli``: the ``collect`` command; ``register(subparsers)`` adds it to the entry point.

Section numbers in this package's comments ("collector design §N", "backfill design §N") refer to
the original design notes of 2026-05, kept on the operator's PC and not in git. The rules they
hold are in docs/business-rules.md (수집) and docs/contracts.md (collect).
"""
