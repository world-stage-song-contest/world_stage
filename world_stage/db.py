import sqlite3
from datetime import datetime
import click
from flask import current_app, g
from pathlib import Path
import csv
import json

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db

def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))

def populate_db():
    db = get_db()
    cur = db.cursor()
    import_path = Path(current_app.root_path) / 'import_data'
    specials_path = import_path / 'specials'
    regular_path = import_path / 'regular'
    points_path = import_path / 'points.json'

    with points_path.open('r', encoding='utf-8') as file:
        points = json.load(file)
        for point_system in points:
            count = point_system['count']
            data = point_system['points']
            data.sort(reverse=True)
            cur.execute('INSERT OR IGNORE INTO point_system (number) VALUES (?) RETURNING id', (count,))
            maybe_point_system_id = cur.fetchone()
            if not maybe_point_system_id:
                continue
            point_system_id = maybe_point_system_id[0]
            for place, point in enumerate(data):
                cur.execute('INSERT OR IGNORE INTO point (place, score, point_system_id) VALUES (?, ?, ?)', (place + 1, point, point_system_id))

    for f in specials_path.iterdir():
        if f.is_file() and f.suffix == '.csv':
            full_name, short_name, vote_options = f.stem.split('-')
            full_name = full_name.strip()
            short_name = short_name.strip()
            vote_options = int(vote_options.strip())

            print(f"Processing {full_name} ({short_name}) with {vote_options} points")

            cur.execute('SELECT id FROM point_system WHERE number = ?', (vote_options,))
            point_system_id = cur.fetchone()
            if not point_system_id:
                continue
            point_system_id = point_system_id[0]

            cur.execute('INSERT OR IGNORE INTO show (show_name, short_name, point_system_id) VALUES (?, ?, ?) RETURNING id', (full_name, short_name, point_system_id))
            maybe_show_id = cur.fetchone()
            if not maybe_show_id:
                continue
            show_id = maybe_show_id[0]

            with open(f, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    username = row['submitter']
                    cur.execute('INSERT OR IGNORE INTO user (username) VALUES (?)', (username,))
                    cur.execute('SELECT id FROM user WHERE username = ?', (username,))
                    user_id = cur.fetchone()[0]

                    country = row['country']
                    cur.execute('INSERT OR IGNORE INTO country (name, code) VALUES (?, \'XX\')', (country,))
                    cur.execute('SELECT id FROM country WHERE name = ?', (country,))
                    country_id = cur.fetchone()[0]

                    title = row['title']
                    artist = row['artist']
                    cur.execute('INSERT INTO song (title, artist, submitter_id, country_id) VALUES (?, ?, ?, ?) RETURNING id', (title, artist, user_id, country_id))
                    song_id = cur.fetchone()[0]

                    running_order = row['ro']
                    cur.execute('INSERT INTO song_show (song_id, show_id, running_order) VALUES (?, ?, ?)', (song_id, show_id, running_order))

    for f in regular_path.iterdir():
        pass
    
    cur.close()
    db.commit()

@click.command('init-db')
def init_db_command():
    """Clear the existing data and create new tables."""
    init_db()
    click.echo('Initialized the database.')

@click.command('populate-db')
def populate_db_command():
    """Populate the database with initial data."""
    populate_db()
    click.echo('Populated the database.')

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(populate_db_command)

sqlite3.register_converter("datetime", lambda x: datetime.fromisoformat(x.decode('utf-8')))