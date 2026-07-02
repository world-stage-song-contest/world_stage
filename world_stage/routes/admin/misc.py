import csv
import datetime
import io
import os
import subprocess
from pathlib import Path

import psycopg
from flask import Response, current_app, request

from ...db import get_db
from ...utils import (
    render_template,
)
from .common import bp


@bp.get("/")
def index():
    return render_template("admin/index.html")

@bp.get("/fuckupdb")
def fuckup_db():
    return render_template("admin/fuckupdb.html")


@bp.post("/fuckupdb")
def fuckup_db_post():
    db = get_db()
    cursor = db.cursor()

    query = request.form.get("query")
    if not query:
        return render_template("admin/fuckupdb.html", error="No query provided"), 400

    subprocess.run(current_app.config.get("BACKUP_SCRIPT", os.environ.get("BACKUP_SCRIPT", "")))

    try:
        cursor.execute("SET ROLE dml_only_role")
        cursor.execute(query)  # type: ignore
        db.commit()

        rows = []
        headers = []
        if cursor.description is not None:
            rows = cursor.fetchall()
            headers = (
                [description[0] for description in cursor.description] if cursor.description else []
            )

        cursor.execute("RESET ROLE")
    except psycopg.Error as e:
        db.rollback()
        return render_template(
            "admin/fuckupdb.html", error=f"Query failed: {str(e)}", query=query
        ), 400

    kind = request.form.get("kind")
    if kind == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

        filename = datetime.datetime.now(tz=datetime.UTC).strftime("query_%Y%m%dT%H%M%SZ.csv")

        response = Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
        return response
    elif kind == "html":
        return render_template("admin/fuckupdb.html", rows=rows, headers=headers, query=query)
    else:
        return render_template("admin/fuckupdb.html", error=f"Unknown filetype: {kind}"), 400


@bp.get("/users")
def users():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT id, username, approved, role
        FROM account
        ORDER BY id
    """)
    users = cursor.fetchall()

    return render_template("admin/users.html", users=users)


@bp.post("/users")
def users_post():
    body = request.get_json()
    if not body:
        return render_template("error.html", error="Empty request body"), 400

    db = get_db()
    cursor = db.cursor()

    user_id = body.get("user_id")
    action = body.get("action")

    if not user_id or not action:
        return render_template("error.html", error="User ID and action must be provided"), 400

    if action == "approve":
        cursor.execute(
            """
            UPDATE account
            SET approved = true
            WHERE id = %s
        """,
            (user_id,),
        )
    elif action == "unapprove":
        cursor.execute(
            """
            UPDATE account
            SET approved = false
            WHERE id = %s
        """,
            (user_id,),
        )
    elif action == "annul_password":
        cursor.execute(
            """
            UPDATE account
            SET password = NULL, salt = NULL
            WHERE id = %s
        """,
            (user_id,),
        )
    else:
        return render_template("error.html", error=f"Unknown action '{action}'"), 400

    db.commit()

    return {"status": "success"}, 200

@bp.get("/upload")
def upload():
    return render_template("admin/upload.html")


@bp.post("/upload")
def upload_post():
    file = request.files.get("file")
    if not file:
        return render_template("error.html", error="No file uploaded"), 400

    file_path = Path(
        current_app.instance_path,
        "uploads",
        file.filename or datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".dat",
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file.save(file_path)

    return render_template(
        "admin/upload.html",
        message=f"File '{file.filename}' uploaded successfully.",
        file_path=str(file_path),
    )


@bp.get("/predictions")
def predictions_index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT show.id, show.show_name, show.short_name, show.year_id AS year,
               COUNT(prediction_set.id) AS prediction_count
        FROM show
        LEFT JOIN prediction_set ON prediction_set.show_id = show.id
        GROUP BY show.id, show.show_name, show.short_name, show.year_id
        ORDER BY show.id
    """)
    shows = cursor.fetchall()

    return render_template("admin/predictions_index.html", shows=shows)
