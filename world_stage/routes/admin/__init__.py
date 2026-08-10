from . import (  # noqa: F401 — importing the modules registers their routes on bp
    changes,
    database,
    draw,
    manage,
    messages,
    metadata,
    misc,
    move,
    recap,
    verifications,
)
from .common import bp

__all__ = ("bp",)
