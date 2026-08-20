import uuid

from hypothesis import given, settings
from hypothesis import strategies as st


def test_schema_api_describes_a_self_consistent_public_schema(client, db, login):
    session_id = login(1)

    @settings(max_examples=20, deadline=None)
    @given(data=st.data(), account_id=st.sampled_from([1, 2, 3]))
    def property_test(data, account_id):
        response = client.get("/admin/fuckupdb/api/schema")

        assert response.status_code == 200
        payload = response.get_json()
        objects = payload["objects"]
        assert objects
        assert all(not item["name"].startswith("pg_") for item in objects)
        selected = data.draw(st.sampled_from(objects), label="schema object")
        column_names = [column["name"] for column in selected["columns"]]
        assert len(column_names) == len(set(column_names))

        if payload["relationships"]:
            relationship = data.draw(
                st.sampled_from(payload["relationships"]), label="relationship"
            )
            described_columns = {
                (item["name"], column["name"]) for item in objects for column in item["columns"]
            }
            assert (
                relationship["source_table"],
                relationship["source_column"],
            ) in described_columns
            assert (
                relationship["target_table"],
                relationship["target_column"],
            ) in described_columns

        options = client.get(
            "/admin/fuckupdb/api/options",
            query_string={"table": "account", "column": "id", "q": account_id},
        )
        assert options.status_code == 200
        assert str(account_id) in {str(option["value"]) for option in options.get_json()["options"]}

    try:
        property_test()
    finally:
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_saved_parameterized_queries_return_the_selected_account_and_are_logged(client, db, login):
    session_id = login(1)

    @settings(max_examples=10)
    @given(account_id=st.sampled_from([1, 2, 3]), limit=st.integers(min_value=1, max_value=50))
    def property_test(account_id, limit):
        query_id = None
        log_id = None
        try:
            saved = client.post(
                "/admin/fuckupdb/api/saved-queries",
                json={
                    "name": f"Property account lookup {uuid.uuid4()}",
                    "description": "Account lookup property",
                    "query_kind": "builder",
                    "definition": {
                        "operation": "select",
                        "from": {"table": "account", "alias": "account"},
                        "columns": [
                            {"kind": "column", "table": "account", "column": "id"},
                            {
                                "kind": "column",
                                "table": "account",
                                "column": "username",
                            },
                        ],
                        "where": {
                            "left": {
                                "kind": "column",
                                "table": "account",
                                "column": "id",
                            },
                            "operator": "eq",
                            "value": {"kind": "parameter", "name": "account_id"},
                        },
                        "limit": {"kind": "literal", "value": limit},
                        "offset": {"kind": "literal", "value": 0},
                    },
                    "parameters": [
                        {
                            "name": "account_id",
                            "label": "Account",
                            "type": "integer",
                            "required": True,
                            "picker": {"table": "account", "column": "id"},
                        }
                    ],
                    "tags": [" Accounts ", "Property", "accounts"],
                },
            )
            assert saved.status_code == 201
            query = saved.get_json()["query"]
            query_id = query["id"]
            assert query["created_by"] == 1
            assert query["tags"] == ["Accounts", "Property"]

            executed = client.post(
                "/admin/fuckupdb/api/execute",
                json={
                    "saved_query_id": query_id,
                    "values": {"account_id": str(account_id)},
                },
            )
            assert executed.status_code == 200
            result = executed.get_json()
            log_id = result["log_id"]
            assert result["rows"] == [
                {
                    "id": account_id,
                    "username": {1: "alice", 2: "bob", 3: "carol"}[account_id],
                }
            ]
            logged = db.execute(
                """SELECT source, operation, success, row_count
                   FROM admin_query_log WHERE id = %s""",
                (log_id,),
            ).fetchone()
            assert logged == {
                "source": "stored_builder",
                "operation": "select",
                "success": True,
                "row_count": 1,
            }
        finally:
            db.rollback()
            if log_id is not None:
                db.execute("DELETE FROM admin_query_log WHERE id = %s", (log_id,))
            if query_id is not None:
                db.execute("DELETE FROM admin_saved_query WHERE id = %s", (query_id,))
            db.commit()

    try:
        property_test()
    finally:
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()


def test_sql_editor_reports_and_logs_the_executed_operation(client, db, login):
    session_id = login(1)
    cases = [
        ("SELECT id FROM account WHERE false", "select"),
        ("UPDATE account SET username = username WHERE false RETURNING id", "update"),
        ("DELETE FROM song WHERE false RETURNING id", "delete"),
    ]

    @given(case=st.sampled_from(cases))
    def property_test(case):
        statement, operation = case
        log_id = None
        try:
            response = client.post(
                "/admin/fuckupdb/api/execute",
                json={"query_kind": "sql", "sql_text": statement},
            )

            assert response.status_code == 200
            result = response.get_json()
            log_id = result["log_id"]
            assert result["row_count"] == 0
            assert db.execute(
                "SELECT operation, success FROM admin_query_log WHERE id = %s",
                (log_id,),
            ).fetchone() == {"operation": operation, "success": True}
        finally:
            db.rollback()
            if log_id is not None:
                db.execute("DELETE FROM admin_query_log WHERE id = %s", (log_id,))
            db.commit()

    try:
        property_test()
    finally:
        db.execute("DELETE FROM session WHERE session_id = %s", (session_id,))
        db.commit()
