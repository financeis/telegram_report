"""Companies (기업 목록·관심 기업): the stock list for the web app and the favorite companies.

Public: ``router`` — ``GET /api/workspace`` and ``PUT /api/favorites/{code}`` (spec §6).
Favorites live in ``~/.review_viewer/favorites.json`` as ``{"stocks": [...]}`` (spec §9.4).
"""
from .router import router
