from typing import Any, Literal

from flask import redirect, request, url_for
from psycopg import sql
from psycopg.types.json import Jsonb

from world_stage.db import get_db
from world_stage.utils import ErrorID, err, render_template, resp
from world_stage.utils.booleans import parse_bool
from world_stage.utils.show_metadata import validate_metadata


def _request_metadata(table: Literal["year", "show"]):
    if request.is_json:
        return request.get_json(silent=True)
    if request.mimetype not in ("application/x-www-form-urlencoded", "multipart/form-data"):
        return None
    metadata: dict[str, Any] = {
        key: values[-1] for key, values in request.form.lists() if key != "return_to"
    }
    if table == "year":
        for key in ("opening", "countdown"):
            if key in metadata and (value := parse_bool(metadata[key])) is not None:
                metadata[key] = value
    else:
        if "opening" in metadata:
            metadata["opening"] = metadata["opening"].strip() or None
        if "intervals" in metadata:
            metadata["intervals"] = [
                line.strip() for value in request.form.getlist("intervals")
                for line in value.splitlines() if line.strip()
            ]
    return metadata


def _manage_url(table: Literal["year", "show"], row_id: int) -> str:
    year_id = row_id
    nf_short_name = None
    if table == "show":
        show = get_db().execute(
            """SELECT show.year_id, nf.short_name AS nf_short_name FROM show
               LEFT JOIN national_final AS nf ON nf.id = show.national_final_id
               WHERE show.id = %s""", (row_id,)
        ).fetchone()
        if show is None:
            return url_for("admin.manage_index")
        year_id = show["year_id"]
        nf_short_name = show["nf_short_name"]
    year = get_db().execute(
        "SELECT special_short_name FROM year WHERE id = %s", (year_id,)
    ).fetchone()
    if year is None:
        return url_for("admin.manage_index")
    if nf_short_name:
        if year["special_short_name"]:
            return url_for(
                "year.manage_special_nf", short_name=year["special_short_name"],
                nf_short_name=nf_short_name,
            )
        return url_for("year.manage_nf", year=year_id, nf_short_name=nf_short_name)
    if year["special_short_name"]:
        return url_for("admin.manage_special", short_name=year["special_short_name"])
    return url_for("admin.manage", year=year_id)


def metadata_response(table: Literal["year", "show"], row_id: int | None, *, write: bool = False):
    if row_id is None:
        return err(ErrorID.NOT_FOUND, f"{table.capitalize()} not found")
    db = get_db()
    if write:
        metadata = _request_metadata(table)
        if error := validate_metadata(metadata, year=table == "year"):
            if request.form.get("return_to") == "manage":
                return render_template("error.html", error=error), 400
            return err(ErrorID.BAD_REQUEST, error)
        value = sql.SQL("metadata || %s") if request.method == "PATCH" else sql.SQL("%s")
        row = db.execute(
            sql.SQL("UPDATE {} SET metadata = {} WHERE id = %s RETURNING metadata").format(
                sql.Identifier(table), value
            ),
            (Jsonb(metadata), row_id),
        ).fetchone()
        db.commit()
    else:
        row = db.execute(
            sql.SQL("SELECT metadata FROM {} WHERE id = %s").format(sql.Identifier(table)),
            (row_id,),
        ).fetchone()
    if row is None:
        return err(ErrorID.NOT_FOUND, f"{table.capitalize()} not found")
    if write and request.form.get("return_to") == "manage":
        return redirect(_manage_url(table, row_id), code=303)
    return resp(row["metadata"])
