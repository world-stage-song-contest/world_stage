from flask import Flask
from hypothesis import given
from hypothesis import strategies as st

from world_stage.performance import init_app, record_sql


@given(
    counts=st.lists(st.integers(min_value=-3, max_value=20), max_size=20),
    durations=st.lists(
        st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=20,
    ),
    expose_headers=st.booleans(),
)
def test_request_metrics_accumulate_positive_work_and_respect_header_policy(
    counts, durations, expose_headers
):
    app = Flask(__name__)
    app.config["PERFORMANCE_HEADERS"] = expose_headers
    init_app(app)

    @app.get("/metrics")
    def metrics():
        for index, count in enumerate(counts):
            record_sql(durations[index % len(durations)], count)
        return "ok"

    response = app.test_client().get("/metrics")

    assert response.status_code == 200
    if expose_headers:
        assert int(response.headers["X-SQL-Query-Count"]) == sum(
            count for count in counts if count > 0
        )
        metric_names = {
            metric.split(";")[0] for metric in response.headers["Server-Timing"].split(", ")
        }
        assert metric_names == {"app", "db"}
    else:
        assert "Server-Timing" not in response.headers
        assert "X-SQL-Query-Count" not in response.headers
