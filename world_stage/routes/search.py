from flask import Blueprint, abort, current_app, redirect, request, url_for
from psycopg.errors import QueryCanceled
from werkzeug.exceptions import RequestEntityTooLarge

from ..db import get_db
from ..search import QueryError, parse_wsql, search_schema
from ..search.choices import CHOICE_FIELDS
from ..search.help import GUIDES, render_guide
from ..search.placeholders import (
    PARAMETER_TYPES,
    analyze_placeholders,
    input_value,
    resolve_placeholders,
)
from ..search.query import Group, Query
from ..search.saved import create_saved, get_saved, list_saved
from ..search.service import execute_search
from ..search.wsql import WsqlError
from ..utils import get_user_id_from_session, render_template, require_user

bp = Blueprint("search", __name__, url_prefix="/search")
PAGE_SIZE = 50
RESULT_LABELS = {
    "entry": "Entry",
    "year": "Year",
    "country": "Country",
    "submitter": "Submitter",
    "artist": "Artist",
    "show": "Show",
}


@bp.get("/help", defaults={"document": "wsql"})
@bp.get("/help/<document>")
def help_page(document):
    if document not in GUIDES:
        abort(404)
    return render_template(
        "search_help.html",
        page_title=GUIDES[document],
        guide=render_guide(document),
        fields=search_schema()["fields"] if document == "wsql" else {},
    )


@bp.route("", methods=["GET", "POST"])
def index():
    request.max_content_length = 256 * 1024
    query_text = ""
    result_type = ""
    result = None
    error = None
    location = None
    offset = 0
    status = 200
    try:
        values = request.form if request.method == "POST" else request.args
        query_text = values.get("q", "")
        result_type = values.get("type", "")
        if request.method == "POST" or "q" in values:
            if result_type not in RESULT_LABELS:
                raise QueryError("Select a page type.")
            if not query_text.strip():
                raise QueryError("Enter a WSQL query.")
            raw_offset = values.get("offset", "0")
            if not raw_offset.isascii() or not raw_offset.isdecimal() or len(raw_offset) > 5:
                raise QueryError("The result offset must be a number from 0 to 10000.")
            offset = int(raw_offset)
            if offset > 10000:
                raise QueryError("The result offset must be a number from 0 to 10000.")
            query = parse_wsql(query_text)
            type_query = parse_wsql(f'type = "{result_type}"')
            query = Query(Group("and", (type_query.where, query.where)), query.order)
            result = execute_search(
                get_db(),
                query,
                limit=PAGE_SIZE,
                offset=offset,
                timezone=current_app.config.get("SEARCH_TIMEZONE", "Europe/Warsaw"),
            )
    except QueryError as exc:
        error = str(exc)
        location = exc.location if isinstance(exc, WsqlError) else None
        status = 400
    except QueryCanceled:
        error = "The search took too long. Add a condition to narrow the results, then try again."
        status = 503
    except RequestEntityTooLarge:
        error = "The search form is too large. Use a shorter query."
        status = 413
    return render_search_page(query_text, result_type, result, error, location, offset), status


def render_search_page(
    query_text="",
    result_type="",
    result=None,
    error=None,
    location=None,
    offset=0,
    editing_saved=None,
):
    next_offset = result["page"]["nextOffset"] if result else None
    if next_offset is not None and next_offset > 10000:
        next_offset = None
    template = (
        "search_response.html" if request.headers.get("X-Search-Fragment") == "1" else "search.html"
    )
    user = get_user_id_from_session(request.cookies.get("session"))
    if user and editing_saved is None and request.method == "POST":
        query_id = request.form.get("editing_saved_id", type=int)
        if query_id:
            editing_saved = get_saved(get_db(), user[0], query_id)
    response = render_template(
        template,
        query_text=query_text,
        result_type=result_type,
        result=result,
        error=error,
        location=location,
        offset=offset,
        previous_offset=max(0, offset - PAGE_SIZE) if result and offset else None,
        next_offset=next_offset,
        result_labels=RESULT_LABELS,
        fields=search_schema()["fields"],
        search_schema=search_schema(),
        choice_fields=sorted(CHOICE_FIELDS),
        can_save=user is not None,
        editing_saved=editing_saved,
        saved_queries=list_saved(get_db(), user[0]) if user and template == "search.html" else [],
    )
    response.vary.update(("Accept", "X-Search-Fragment"))
    response.cache_control.private = True
    return response


def check_form_origin():
    origin = request.headers.get("Origin")
    if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
        origin and origin != request.host_url.rstrip("/")
    ):
        abort(403)


def parameter_form(mode, *, parameters, source="", result_type="", saved=None, error=None):
    template = (
        "search_parameters_form.html"
        if request.headers.get("X-Search-Dialog")
        else "search_parameters.html"
    )
    values = {}
    if mode == "save" and saved:
        values["name"] = saved["name"]
        for name, definition in saved["parameters"].items():
            values["parameter_type." + name] = definition["type"]
            if "default" in definition:
                values["default_enabled." + name] = "on"
                value = definition["default"]
                if value is None:
                    values["default_null." + name] = "on"
                values["default." + name] = (
                    value
                    if isinstance(value, str)
                    else "NULL"
                    if value is None
                    else str(value).lower()
                )
    if request.endpoint == "search.confirm_save":
        values = dict(request.form)
    response = render_template(
        template,
        mode=mode,
        parameters=parameters,
        source=source,
        result_type=result_type,
        saved=saved,
        error=error,
        values=values if mode == "save" else request.form,
        parameter_types=PARAMETER_TYPES,
    )
    response.cache_control.no_store = True
    response.vary.update(("Accept", "X-Search-Dialog"))
    return response


@bp.post("/save")
@require_user(redirect_to_login=True)
def prepare_save(user):
    request.max_content_length = 256 * 1024
    source, result_type = request.form.get("q", ""), request.form.get("type", "")
    saved = editing_query(user)
    try:
        if result_type not in RESULT_LABELS:
            raise QueryError("Select a page type")
        parameters = analyze_placeholders(source)
    except QueryError as exc:
        return parameter_form("invalid", parameters=[], error=str(exc)), 400
    return parameter_form(
        "save", parameters=parameters, source=source, result_type=result_type, saved=saved
    )


def editing_query(user):
    query_id = request.form.get("editing_saved_id", type=int)
    if query_id:
        saved = get_saved(get_db(), user[0], query_id)
        if saved is None:
            abort(404)
        return saved
    return None


@bp.post("/save/confirm")
@require_user(redirect_to_login=True)
def confirm_save(user):
    request.max_content_length = 256 * 1024
    check_form_origin()
    source, result_type = request.form.get("q", ""), request.form.get("type", "")
    existing = editing_query(user)
    parameters = []
    try:
        parameters = analyze_placeholders(source)
        settings = {}
        for parameter in parameters:
            name = parameter["name"]
            type_name = request.form.get("parameter_type." + name, "")
            settings[name] = {"type": type_name}
            if request.form.get("default_enabled." + name):
                settings[name]["default"] = (
                    None
                    if request.form.get("default_null." + name)
                    else input_value(type_name, request.form.get("default." + name, ""))
                )
        saved = create_saved(
            get_db(),
            user[0],
            request.form.get("name", ""),
            result_type,
            source,
            settings,
            query_id=existing["id"] if existing else None,
        )
    except QueryError as exc:
        return parameter_form(
            "save",
            parameters=parameters,
            source=source,
            result_type=result_type,
            saved=existing,
            error=str(exc),
        ), 400
    if request.headers.get("X-Search-Dialog"):
        return {"saved": saved}, 200 if existing else 201
    return redirect(url_for("search.index"))


@bp.route("/use", methods=["GET", "POST"])
@require_user(redirect_to_login=True)
def use_saved(user):
    request.max_content_length = 256 * 1024
    query_id = request.values.get("saved_id", type=int)
    saved = get_saved(get_db(), user[0], query_id) if query_id else None
    if saved is None:
        abort(404)
    try:
        missing = resolve_placeholders(saved["query_text"], saved["parameters"])["missing"]
    except QueryError as exc:
        return parameter_form("invalid", parameters=[], saved=saved, error=str(exc)), 400
    try:
        values = {
            parameter["name"]: input_value(
                parameter["type"], request.form["value." + parameter["name"]]
            )
            for parameter in missing
            if "value." + parameter["name"] in request.form
        }
        resolved = resolve_placeholders(saved["query_text"], saved["parameters"], values)
    except QueryError as exc:
        return parameter_form("use", parameters=missing, saved=saved, error=str(exc)), 400
    if resolved["missing"]:
        return parameter_form("use", parameters=missing, saved=saved)
    query_text = resolved["query"]
    assert isinstance(query_text, str)
    if request.headers.get("X-Search-Dialog"):
        return {"query": query_text, "type": saved["result_type"]}
    return render_search_page(query_text, saved["result_type"])


@bp.get("/edit")
@require_user(redirect_to_login=True)
def edit_saved(user):
    query_id = request.args.get("saved_id", type=int)
    saved = get_saved(get_db(), user[0], query_id) if query_id else None
    if saved is None:
        abort(404)
    if request.headers.get("X-Search-Dialog"):
        return {
            "query": saved["query_text"],
            "type": saved["result_type"],
            "editingSavedId": saved["id"],
        }
    return render_search_page(saved["query_text"], saved["result_type"], editing_saved=saved)
