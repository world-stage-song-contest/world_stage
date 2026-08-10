import datetime
import uuid


def _login(client, db, user_id=1):
    session_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO session (user_id, session_id, expires_at)
        VALUES (%s, %s, %s)
        """,
        (user_id, session_id, datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)),
    )
    db.commit()
    client.set_cookie("session", session_id)
    return session_id


def test_database_schema_exposes_columns_and_relationships(client, db):
    session_id = _login(client, db)
    try:
        response = client.get("/admin/fuckupdb/api/schema")
        assert response.status_code == 200
        payload = response.get_json()
        assert not any(item["name"].startswith("pg_") for item in payload["objects"])
        account = next(item for item in payload["objects"] if item["name"] == "account")
        assert {column["name"] for column in account["columns"]} >= {"id", "username"}
        assert any(
            relation["source_table"] == "current_song"
            and relation["source_column"] == "submitter_id"
            and relation["target_table"] == "account"
            for relation in payload["relationships"]
        )
        options = client.get("/admin/fuckupdb/api/options?table=account&column=id&q=1")
        assert options.status_code == 200
        assert {item["value"] for item in options.get_json()["options"]} == {"1"}
    finally:
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_saved_parameterized_query_executes_and_is_logged(client, db):
    session_id = _login(client, db)
    name = f"test account lookup {uuid.uuid4()}"
    query_id = None
    log_id = None
    definition = {
        "operation": "select",
        "from": {"table": "account", "alias": "account"},
        "columns": [
            {"kind": "column", "table": "account", "column": "id"},
            {"kind": "column", "table": "account", "column": "username"},
        ],
        "where": {
            "left": {"kind": "column", "table": "account", "column": "id"},
            "operator": "eq",
            "value": {"kind": "parameter", "name": "account_id"},
        },
        "limit": {"kind": "literal", "value": 10},
        "offset": {"kind": "literal", "value": 0},
    }
    parameters = [
        {
            "name": "account_id",
            "label": "Account",
            "type": "integer",
            "required": True,
            "picker": {"table": "account", "column": "id"},
        }
    ]
    try:
        saved = client.post(
            "/admin/fuckupdb/api/saved-queries",
            json={
                "name": name,
                "description": "integration test",
                "query_kind": "builder",
                "definition": definition,
                "parameters": parameters,
            },
        )
        assert saved.status_code == 201, saved.get_data(as_text=True)
        query_id = saved.get_json()["query"]["id"]

        response = client.post(
            "/admin/fuckupdb/api/execute",
            json={"saved_query_id": query_id, "values": {"account_id": "1"}},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        payload = response.get_json()
        log_id = payload["log_id"]
        assert payload["rows"] == [{"id": 1, "username": "alice"}]

        logged = db.execute(
            """
            SELECT source, operation, success, row_count, parameter_schema
            FROM admin_query_log WHERE id = %s
            """,
            (log_id,),
        ).fetchone()
        assert logged["source"] == "stored_builder"
        assert logged["operation"] == "select"
        assert logged["success"] is True
        assert logged["row_count"] == 1
        assert logged["parameter_schema"][0]["name"] == "account_id"
    finally:
        db.rollback()
        if query_id is not None:
            db.execute("DELETE FROM admin_saved_query WHERE id = %s", (query_id,))
        if log_id is not None:
            db.execute("DELETE FROM admin_query_log WHERE id = %s", (log_id,))
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_sql_editor_executes_and_logs_delete(client, db):
    session_id = _login(client, db)
    log_id = None
    try:
        response = client.post(
            "/admin/fuckupdb/api/execute",
            json={
                "query_kind": "sql",
                "sql_text": "DELETE FROM song WHERE false RETURNING id",
            },
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        payload = response.get_json()
        log_id = payload["log_id"]
        assert payload["row_count"] == 0
        logged = db.execute(
            "SELECT operation, success FROM admin_query_log WHERE id = %s", (log_id,)
        ).fetchone()
        assert logged == {"operation": "delete", "success": True}
    finally:
        db.rollback()
        if log_id is not None:
            db.execute("DELETE FROM admin_query_log WHERE id = %s", (log_id,))
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()
