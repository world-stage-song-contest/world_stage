import datetime as dt

from flask import url_for

from .compiler import compile_search
from .query import Query, QueryError, encode_query


def result_url(row):
    category, data = row["type"], row["data"]
    if category == "entry":
        args = {"code": data["country"].lower(), "entry_number": data["entryNumber"]}
        if data["special"]:
            return url_for("country.special_details", special_short_name=data["special"], **args)
        return url_for("country.details", year=data["year"], **args)
    if category in ("year", "show"):
        if data["special"]:
            endpoint = "year.special" if category == "year" else "year.special_results"
            args = {"short_name": data["special"]}
        else:
            endpoint = "year.year" if category == "year" else "year.results"
            args = {"year": data["year"]}
        if category == "show":
            args["show"] = data["show"]
        return url_for(endpoint, **args)
    if category == "country":
        return url_for("country.country", code=data["country"].lower())
    if category == "submitter":
        return url_for("user.submissions", username=data["username"])
    return url_for("artist.details", name=data["name"])


def execute_search(
    connection, query: Query, *, limit=50, offset=0, timezone="Europe/Warsaw", now=None
):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise QueryError("Page limit must be an integer from 1 to 100", "/page/limit")
    if type(offset) is not int or not 0 <= offset <= 10000:
        raise QueryError("Page offset must be an integer from 0 to 10000", "/page/offset")
    evaluated_at = now or dt.datetime.now(dt.UTC)
    compiled = compile_search(
        query, limit=limit + 1, offset=offset, now=evaluated_at, timezone=timezone
    )
    with connection.transaction():
        previous = connection.execute(
            "SELECT current_setting('statement_timeout') AS timeout, "
            "current_setting('TimeZone') AS timezone"
        ).fetchone()
        connection.execute("SELECT set_config('statement_timeout', '5000', true)")
        connection.execute("SELECT set_config('TimeZone', %s, true)", (timezone,))
        rows = connection.execute(compiled.statement, compiled.params).fetchall()
        connection.execute(
            "SELECT set_config('statement_timeout', %s, true), set_config('TimeZone', %s, true)",
            (previous["timeout"], previous["timezone"]),
        )
    results = [
        {
            "type": row["type"],
            "id": row["id"],
            "title": row["name"],
            "url": result_url(row),
            "relevance": row["relevance"],
            "data": row["data"],
        }
        for row in rows[:limit]
    ]
    return {
        "query": encode_query(query),
        "results": results,
        "page": {
            "limit": limit,
            "offset": offset,
            "nextOffset": offset + limit if len(rows) > limit else None,
        },
        "context": {"timezone": timezone, "evaluatedAt": evaluated_at.isoformat()},
    }
