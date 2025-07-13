import sqlite3
import datetime
import click
from flask import current_app, g
from pathlib import Path

def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
        g.db.set_trace_callback(print)

    return g.db

def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql', "r") as f:
        db.executescript(f.read())

def migrate_db() -> list[str]:
    db = get_db()
    cur = db.cursor()
    migrations_path = Path(current_app.root_path) / 'migrations'

    applied_migrations = []
    for f in sorted(migrations_path.iterdir()):
        if f.is_file() and f.suffix == '.sql':
            name = f.stem
            cur.execute('SELECT * FROM migration WHERE name = ?', (name,))
            if cur.fetchone():
                continue
            with f.open() as file:
                sql = file.read()
                db.executescript(sql)
            cur.execute('INSERT INTO migration (name) VALUES (?)', (name,))
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
    else:
        click.echo('No migrations to apply.')

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(migrate_db_command)

sqlite3.register_converter("datetime", lambda x: datetime.datetime.fromisoformat(x.decode('utf-8')).replace(tzinfo=datetime.timezone.utc))
sqlite3.register_converter("date", lambda x: datetime.date.fromisoformat(x.decode('utf-8')))