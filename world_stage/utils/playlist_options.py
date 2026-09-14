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
    values = {"postcards": True, "host": True, "intervals": False}
    suffixes = {"np": "postcards"}
    if show:
        suffixes.update(nh="host", ni="intervals")
    stem = key
    while stem.rsplit("-", 1)[-1] in suffixes and "-" in stem:
        stem, suffix = stem.rsplit("-", 1)
        values[suffixes[suffix]] = False
    parameters = set(suffixes.values())
    for parameter in parameters:
        values[parameter] = query_bool(args, parameter, values[parameter])
    filename = key
    if parameters.intersection(key.casefold() for key in args):
        filename = stem + "".join(
            f"-{suffix}" for suffix, parameter in sorted(suffixes.items())
            if not values[parameter]
        )
    return PlaylistOptions(stem, filename, **values)
