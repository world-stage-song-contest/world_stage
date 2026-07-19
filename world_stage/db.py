import sys
import time
import typing
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self

import click
import psycopg
from flask import current_app, g
from psycopg.abc import Params, Query, QueryNoTemplate
from psycopg_pool import ConnectionPool

from .performance import record_sql


class InstrumentedCursor(psycopg.Cursor[dict[str, Any]]):
    """A regular psycopg cursor that records SQL work on the active request."""

    def execute(
        self,
        query: Query,
        params: Params | None = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Self:
        started_at = time.perf_counter()
        try:
            return super().execute(
                typing.cast(QueryNoTemplate, query),
                params,
                prepare=prepare,
                binary=binary,
            )
        finally:
            record_sql(time.perf_counter() - started_at)

    def executemany(
        self,
        query: Query,
        params_seq: Iterable[Params],
        *,
        returning: bool = False,
    ) -> None:
        statement_count = 0

        def counted_params() -> Iterable[Params]:
            nonlocal statement_count
            for params in params_seq:
                statement_count += 1
                yield params

        started_at = time.perf_counter()
        try:
            return super().executemany(query, counted_params(), returning=returning)
        finally:
            record_sql(time.perf_counter() - started_at, statement_count)


def fetchone(cursor: psycopg.Cursor[dict[str, Any]]) -> dict[str, Any]:
    """Fetch one row, raising if the query returned no rows.

    Use for queries that are guaranteed to return a result
    (e.g. SELECT COUNT, INSERT ... RETURNING)."""
    row = cursor.fetchone()
    assert row is not None, "expected a row but got None"
    return row


def get_db() -> psycopg.Connection[dict[str, Any]]:
    if "db" not in g:
        pool: ConnectionPool = current_app.config["DB_POOL"]
        g.db = pool.getconn()

    return typing.cast(psycopg.Connection[dict[str, Any]], g.db)


def close_db(e=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        pool: ConnectionPool = current_app.config["DB_POOL"]
        db.rollback()
        pool.putconn(db)


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql", "r") as f:
        db.execute(f.read())


def migrate_db() -> list[str]:
    applied_migrations = []

    # Schema migrations (committed to repo) + instance-specific data migrations
    # (excluded from git, e.g. show/song/vote imports). Files from both
    # directories are merged and run in filename (timestamp) order.
    migration_dirs = [
        Path(current_app.root_path) / "migrations",
        Path(current_app.instance_path) / "migrations",
    ]

    candidates = []
    for d in migration_dirs:
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and f.suffix == ".sql":
                    candidates.append(f)
    candidates.sort(key=lambda p: p.name)

    def _on_notice(diag):
        click.echo(f"[{diag.severity}] {diag.message_primary}", err=True)

    with current_app.config["DB_POOL"] as pool, pool.getconn() as db, db.cursor() as cur:
        db.add_notice_handler(_on_notice)
        for f in candidates:
            name = f.stem
            cur.execute("SELECT * FROM migration WHERE name = %s", (name,))
            if cur.fetchone():
                continue
            with f.open() as file:
                sql = file.read()
                db.execute(typing.cast(typing.LiteralString, sql))
            cur.execute("INSERT INTO migration (name) VALUES (%s)", (name,))
            db.commit()
            applied_migrations.append(name)

    return applied_migrations


@click.command("init-db")
def init_db_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo("Initialized the database.")


@click.command("migrate-db")
def migrate_db_command():
    """Run the database migrations."""
    applied = migrate_db()
    if applied:
        click.echo("Applied migrations: " + ", ".join(applied))
    else:
        click.echo("No migrations to apply.")

    sys.exit(0)


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_db_command)
