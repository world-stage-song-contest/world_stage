import logging

from world_stage.db import get_db


def test_request_performance_metrics_count_execute_and_executemany(app, caplog):
    app.config["PERFORMANCE_HEADERS"] = True

    @app.get("/_test/performance-metrics")
    def performance_metrics():
        cursor = get_db().cursor()
        cursor.execute("SELECT 1")
        cursor.executemany("SELECT %s", [(1,), (2,), (3,)])
        return "ok"

    with caplog.at_level(logging.INFO, logger="world_stage.performance"):
        response = app.test_client().get("/_test/performance-metrics")

    assert response.status_code == 200
    assert response.headers["X-SQL-Query-Count"] == "4"
    assert "app;dur=" in response.headers["Server-Timing"]
    assert "db;dur=" in response.headers["Server-Timing"]

    records = [
        record for record in caplog.records if record.name == "world_stage.performance"
    ]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "endpoint=performance_metrics" in message
    assert "status=200" in message
    assert "sql_count=4" in message


def test_performance_headers_are_disabled_by_default(app):
    app.config["PERFORMANCE_HEADERS"] = False

    @app.get("/_test/performance-no-headers")
    def performance_no_headers():
        return "ok"

    response = app.test_client().get("/_test/performance-no-headers")

    assert "Server-Timing" not in response.headers
    assert "X-SQL-Query-Count" not in response.headers
