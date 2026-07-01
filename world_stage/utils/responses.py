import io
import json
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable
from enum import Enum
from typing import Any

import flask
from flask import Response, request


def create_cookie(**kwargs: str) -> str:
    cookie = []
    for key, value in kwargs.items():
        value = urllib.parse.quote(value)
        cookie.append(f"{key}={value}")
    return "&".join(cookie)


def parse_cookie(cookie: str) -> dict[str, str]:
    cookie_dict: dict[str, str] = defaultdict(str)
    if not cookie:
        return cookie_dict

    for item in cookie.split("&"):
        key, value = item.split("=")
        cookie_dict[key] = urllib.parse.unquote(value)
    return cookie_dict


def render_template(template: str, **kwargs) -> Response:
    resp = Response()
    if request.accept_mimetypes.accept_html:
        resp.data = flask.render_template(template, **kwargs)
        resp.content_type = "text/html"
    elif request.accept_mimetypes.accept_json:
        resp.data = json.dumps(kwargs)
        resp.content_type = "application/json"
    else:
        resp.data = (f"Invalid format. Accepted MIME types are: [{request.accept_mimetypes}] "
                     f"for UA '{request.headers.get('User-Agent', '')}'")
        resp.content_type = "text/plain"

    return resp


def resp(data: Any, code: int = 200) -> tuple[dict[str, Any], int]:
    return {"result": data}, code


class ErrorID(Enum):
    NONE = 0
    NOT_FOUND = 1
    UNAUTHORIZED = 2
    FORBIDDEN = 3
    BAD_REQUEST = 4
    CONFLICT = 5

    def http_code(self):
        match self:
            case ErrorID.NONE:
                return 200
            case ErrorID.NOT_FOUND:
                return 404
            case ErrorID.UNAUTHORIZED:
                return 401
            case ErrorID.FORBIDDEN:
                return 403
            case ErrorID.BAD_REQUEST:
                return 400
            case ErrorID.CONFLICT:
                return 409
            case _:
                return 400


def err(id: ErrorID, desc: str) -> tuple[dict[str, Any], int]:
    return ({"error": {"id": id.value, "description": desc}}, id.http_code())


def url_bool(datum: str) -> bool:
    return datum in ("true", "1", "y", "on", "yes")


def write_m3u(
    entries: Iterable[tuple[str, str | None]], postcards: bool = False
) -> tuple[str, list[str]]:
    """Emit an .m3u from (cc, video_link) pairs in the order given. When
    postcards is True, each entry is preceded by a postcard video. Returns
    (text, bad_ccs) where bad_ccs collects country codes whose link is empty
    or not hosted on media.world-stage.org."""
    output = io.StringIO(newline="\r\n")
    output.write("#EXTM3U\n")
    bad: list[str] = []
    for cc, url in entries:
        url = url or ""
        if postcards:
            output.write("#EXTINF:0\n")
            output.write("#EXTVLCOPT:network-caching=3000\n")
            output.write(f"https://media.world-stage.org/postcards/{cc.lower()}.mov\n")
        output.write("#EXTINF:0\n")
        output.write("#EXTVLCOPT:network-caching=3000\n")
        if "media.world-stage.org" not in url:
            bad.append(cc)
        output.write((url or "BAD LINK REPLACE ME THIS IS A BUG") + "\n")
    return output.getvalue(), bad
