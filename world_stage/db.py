import sys
from typing import Any
import typing
import psycopg
import click
from flask import current_app, g
from pathlib import Path

from psycopg_pool import ConnectionPool

def get_db() -> psycopg.Connection[dict[str, Any]]:
    if "db" not in g:
        pool: ConnectionPool = current_app.config["DB_POOL"]
        g.db = pool.getconn()

    return typing.cast(psycopg.Connection[dict[str, Any]], g.db)

def close_db(e=None) -> None:
    db = g.pop('db', None)
    if db is not None:
        pool: ConnectionPool = current_app.config["DB_POOL"]
        db.rollback()
        pool.putconn(db)

def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql', "r") as f:
        db.execute(f.read())

def migrate_db() -> list[str]:
    applied_migrations = []

    with get_db() as db:
        with db.cursor() as cur:
            migrations_path = Path(current_app.root_path) / 'migrations'

            for f in sorted(migrations_path.iterdir()):
                if f.is_file() and f.suffix == '.sql':
                    name = f.stem
                    cur.execute('SELECT * FROM migration WHERE name = %s', (name,))
                    if cur.fetchone():
                        continue
                    with f.open() as file:
                        sql = file.read()
                        db.execute(typing.cast(typing.LiteralString, sql))
                    cur.execute('INSERT INTO migration (name) VALUES (%s)', (name,))
                    db.commit()
                    applied_migrations.append(name)

    return applied_migrations

@click.command('init-db')
def init_db_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo('Initialized the database.')

@click.command('migrate-db')
def migrate_db_command():
    """Run the database migrations."""
    applied = migrate_db()
    if applied:
        click.echo('Applied migrations: ' + ', '.join(applied))
        sys.exit(0)
    else:
        click.echo('No migrations to apply.')
        sys.exit(1)

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_db_command)