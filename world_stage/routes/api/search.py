from flask import Blueprint, current_app, request
from psycopg.errors import QueryCanceled
from werkzeug.exceptions import BadRequest

from ...db import get_db
from ...search import QueryError, convert_query, parse_search_query, search_schema
from ...search.choices import search_choices
from ...search.placeholders import analyze_placeholders, resolve_placeholders
from ...search.query import object_keys
from ...search.saved import create_saved, get_saved, list_saved
from ...search.service import execute_search
from ...search.wsql import WsqlError
from ...utils import require_api_auth
from ...utils.responses import resp

bp = Blueprint("search", __name__, url_prefix="/search")


@bp.get("/schema")
def schema():
    return resp(search_schema())


@bp.get("/choices")
def choices():
    return resp(search_choices(get_db(), request.args.get("type"), request.args.get("field")))


@bp.errorhandler(QueryError)
def query_error(exc):
    error = {"code": exc.code, "description": str(exc), "path": exc.path}
    if isinstance(exc, WsqlError):
        error["location"] = exc.location
    return {"error": error}, 400


@bp.errorhandler(BadRequest)
def invalid_json(exc):
    return {"error": {"code": "invalid_json", "description": "Invalid JSON document"}}, 400


@bp.errorhandler(QueryCanceled)
def search_timeout(exc):
    return {
        "error": {"code": "search_timeout", "description": "Search exceeded its time limit"}
    }, 503


def request_body(required, optional=()):
    request.max_content_length = 65536
    if not request.is_json:
        raise QueryError("Expected application/json")
    try:
        body = request.get_json()
    except RecursionError as exc:
        raise BadRequest("Invalid JSON document") from exc
    object_keys(body, required, optional, "")
    return body


@bp.post("")
def search():
    body = request_body({"query"}, {"page"})
    query = parse_search_query(body["query"])
    page = body.get("page", {})
    object_keys(page, set(), {"limit", "offset"}, "/page")
    return resp(
        execute_search(
            get_db(),
            query,
            limit=page.get("limit", 50),
            offset=page.get("offset", 0),
            timezone=current_app.config.get("SEARCH_TIMEZONE", "Europe/Warsaw"),
        )
    )


@bp.post("/convert")
def convert():
    body = request_body({"query", "to"})
    return resp({"format": body["to"], "query": convert_query(body["query"], body["to"])})


@bp.post("/placeholders")
def placeholders():
    body = request_body({"query"}, {"types"})
    return resp(analyze_placeholders(body["query"], body.get("types")))


@bp.get("/saved")
@require_api_auth
def saved_list(auth):
    return resp(list_saved(get_db(), auth[0]))


@bp.post("/saved")
@require_api_auth
def saved_create(auth):
    body = request_body({"name", "type", "query"}, {"parameters"})
    return resp(
        create_saved(
            get_db(), auth[0], body["name"], body["type"], body["query"], body.get("parameters", {})
        ),
        201,
    )


@bp.post("/saved/<int:query_id>/resolve")
@require_api_auth
def saved_resolve(query_id, auth):
    body = request_body(set(), {"values"})
    saved = get_saved(get_db(), auth[0], query_id)
    if saved is None:
        return {"error": {"description": "Saved query not found"}}, 404
    result = resolve_placeholders(saved["query_text"], saved["parameters"], body.get("values"))
    return resp(result | {"type": saved["result_type"]})


@bp.get("/saved/<int:query_id>")
@require_api_auth
def saved_get(query_id, auth):
    saved = get_saved(get_db(), auth[0], query_id)
    if saved is None:
        return {"error": {"description": "Saved query not found"}}, 404
    return resp(saved)


@bp.put("/saved/<int:query_id>")
@require_api_auth
def saved_update(query_id, auth):
    if get_saved(get_db(), auth[0], query_id) is None:
        return {"error": {"description": "Saved query not found"}}, 404
    body = request_body({"name", "type", "query"}, {"parameters"})
    return resp(
        create_saved(
            get_db(),
            auth[0],
            body["name"],
            body["type"],
            body["query"],
            body.get("parameters", {}),
            query_id=query_id,
        )
    )


@bp.after_request
def private_saved_responses(response):
    if "/saved" in request.path:
        response.cache_control.no_store = True
    return response
