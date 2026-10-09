"""MongoDB reads: a collection handle and a reachability check.

pymongo is imported inside the functions, never at the top of this module:
every command imports core when it starts (``python -m research_desk``), and
only the work that reads MongoDB should load the driver.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _client(url: str, timeout_ms: int) -> Any:
    import pymongo

    # serverSelectionTimeoutMS: how long an operation waits for a reachable
    # server before it fails; connectTimeoutMS: one TCP connection attempt.
    return pymongo.MongoClient(url, serverSelectionTimeoutMS=timeout_ms, connectTimeoutMS=timeout_ms)


def mongo_collection(url: str, db: str, name: str, timeout_ms: int = 5000) -> Any:
    """pymongo Collection ``name`` of database ``db`` at ``url``, for reading.

    Nothing connects until the first query. If no server answers within
    ``timeout_ms``, that query raises pymongo's ServerSelectionTimeoutError.
    The handle owns its client: ``handle.database.client.close()`` ends it.
    """
    return _client(url, timeout_ms)[db][name]


def ping(url: str, timeout_ms: int = 3000) -> bool:
    """True when the MongoDB server at ``url`` answers ``ping`` within ``timeout_ms``.

    An unreachable server, a refused login or an unusable URL gives False, not
    an error. The reason is logged by error type only: a URL can hold a password.
    """
    from pymongo.errors import PyMongoError

    client = None
    try:
        client = _client(url, timeout_ms)
        client.admin.command("ping")
        return True
    except PyMongoError as exc:
        logger.debug("MongoDB ping failed: %s", type(exc).__name__)
        return False
    finally:
        if client is not None:
            client.close()
