"""Web assembly (웹 서버 조립): the common devices, the error answers, the screen files and the
feature registration list. The web layer does no business work: it only registers the features.

- ``app``: ``create_app(dist=None)`` builds the FastAPI app; ``FEATURES`` is the registration list.
- ``server``: the ``web`` command (``register(subparsers)``, ``serve(args)``).

Import the modules themselves. This file imports nothing, so the command entry can load
``server`` without loading FastAPI and every feature for the commands other than ``web``.
"""
