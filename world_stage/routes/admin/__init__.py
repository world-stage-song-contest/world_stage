from . import (  # noqa: F401 — importing the modules registers their routes on bp
    changes,
    draw,
    manage,
    metadata,
    misc,
    move,
    recap,
)
from .common import bp

__all__ = ("bp",)
