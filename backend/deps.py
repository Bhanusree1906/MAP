"""FastAPI dependency wiring.

Reuses database.session.get_db directly - it's already a request-scoped
generator dependency in the shape FastAPI expects.
"""

from __future__ import annotations

from database.session import get_db

__all__ = ["get_db"]
