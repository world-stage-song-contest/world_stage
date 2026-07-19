import logging
import time
from dataclasses import dataclass

from flask import Flask, Response, current_app, g, has_request_context, request

log = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    started_at: float
    sql_count: int = 0
    sql_duration: float = 0.0


def record_sql(duration: float, count: int = 1) -> None:
    """Add completed SQL work to the active request, if there is one."""
    if not has_request_context() or count <= 0:
        return

    metrics: RequestMetrics | None = g.get("request_metrics")
    if metrics is None:
        return
    metrics.sql_count += count
    metrics.sql_duration += duration


def _start_request_metrics() -> None:
    g.request_metrics = RequestMetrics(started_at=time.perf_counter())


def _finish_request_metrics(response: Response) -> Response:
    metrics: RequestMetrics | None = g.get("request_metrics")
    if metrics is None:
        return response

    duration_ms = (time.perf_counter() - metrics.started_at) * 1_000
    sql_duration_ms = metrics.sql_duration * 1_000

    log.info(
        "request method=%s endpoint=%s status=%d duration_ms=%.2f "
        "sql_count=%d sql_duration_ms=%.2f",
        request.method,
        request.endpoint or "-",
        response.status_code,
        duration_ms,
        metrics.sql_count,
        sql_duration_ms,
    )

    if current_app.config["PERFORMANCE_HEADERS"]:
        response.headers["Server-Timing"] = (
            f"app;dur={duration_ms:.2f}, db;dur={sql_duration_ms:.2f}"
        )
        response.headers["X-SQL-Query-Count"] = str(metrics.sql_count)

    return response


def init_app(app: Flask) -> None:
    app.before_request(_start_request_metrics)
    app.after_request(_finish_request_metrics)
