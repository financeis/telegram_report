"""Tagger: pending report rows → auto / review_needed (8-node LangGraph row graph).

Command: ``python -m research_desk tag run|inspect|escalate|reset-worker|requeue``.
``requeue`` sends classified rows that meet chosen criteria back to pending (no AI call).
``research_desk.tagger.cli.register(subparsers)`` adds it to the command list.
"""
