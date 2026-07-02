from . import (  # noqa: F401 — importing the modules registers their routes on bp
    overview,
    penalty,
    play,
    predictions,
    qualifiers,
    results,
    scoreboard,
    song_votes,
    voters,
)
from .common import bp
from .play import generate_playlist

__all__ = ("bp", "generate_playlist")
