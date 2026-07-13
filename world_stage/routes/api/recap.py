import hashlib
import json

from flask import Blueprint, make_response, request

from world_stage.routes.admin.recap import drop_none, get_recap_data
from world_stage.utils import ErrorID, err, resp

bp = Blueprint("recap", __name__)

_RECAP_TYPES = frozenset(("show", "year", "country", "submitter"))


def _recap_etag(data: list[dict]) -> str:
    """Hash the response values independently of database row ordering."""
    rows = sorted(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        for row in data
    )
    return hashlib.sha256(f"[{','.join(rows)}]".encode()).hexdigest()


@bp.get("/recap")
def recap():
    """Return recap data for one or more shows, years, countries, or submitters.

    The ``type`` query parameter selects the variant. Repeat ``show`` to
    request multiple values, for example ``?type=show&show=2025-sf1``. Set
    Set ``specials=true`` to include special-year entries, or ``specials=only``
    to return only special-year entries. It is false by default.
    """
    recap_type = request.args.get("type", "")
    selections = request.args.getlist("show")
    specials = request.args.get("specials", "false").lower()
    if recap_type not in _RECAP_TYPES:
        return err(ErrorID.BAD_REQUEST, "type must be show, year, country, or submitter")
    if not selections:
        return err(ErrorID.BAD_REQUEST, "At least one show parameter is required")
    if specials not in ("false", "true", "only"):
        return err(ErrorID.BAD_REQUEST, "specials must be false, true, or only")

    data = get_recap_data(
        recap_type, selections, specials=specials, include_change_metadata=True
    )
    if data is None:
        return err(ErrorID.BAD_REQUEST, "One or more recap selections are invalid")

    changed_at = max((row.pop("_changed_at") for row in data if row["_changed_at"]), default=None)
    result = drop_none(data)
    response = make_response(resp(result))
    response.set_etag(_recap_etag(result))
    if changed_at is not None:
        response.last_modified = changed_at
    if (
        "If-Match" not in request.headers
        and request.if_unmodified_since is not None
        and response.last_modified is not None
        and response.last_modified > request.if_unmodified_since
    ):
        response.status_code = 412
        response.set_data(b"")
        return response
    response.make_conditional(request)
    return response
