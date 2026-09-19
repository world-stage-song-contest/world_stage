from dataclasses import dataclass

from werkzeug.datastructures import MultiDict

from .booleans import query_bool


@dataclass(frozen=True)
class PlaylistOptions:
    stem: str
    filename: str
    postcards: bool
    host: bool
    intervals: bool


def playlist_options(key: str, args: MultiDict, *, show: bool = False) -> PlaylistOptions:
    values = {"postcards": False, "host": False, "intervals": False}
    suffixes = {"np": "postcards"}
    if show:
        suffixes.update(nh="host", ni="intervals")
    stem = key
    while stem.rsplit("-", 1)[-1] in suffixes and "-" in stem:
        stem = stem.rsplit("-", 1)[0]
    for parameter in suffixes.values():
        values[parameter] = query_bool(args, parameter, False)
    filename = stem + "".join(
        f"-{suffix}" for suffix, parameter in sorted(suffixes.items())
        if not values[parameter]
    )
    return PlaylistOptions(stem, filename, **values)
