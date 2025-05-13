import sqlite3
import datetime
import click
import unicodedata
from flask import current_app, g
from pathlib import Path
import csv
import json

def get_db() -> sqlite3.Connection:
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

def populate_db():
    db = get_db()
    cur = db.cursor()
    import_path = Path(current_app.root_path) / 'import_data'
    specials_path = import_path / 'specials'
    regular_path = import_path / 'submissions.txt'
    points_path = import_path / 'points.json'
    countries_path = import_path / 'countries.csv'
    alternatives_path = import_path / 'alternative_names.csv'
    votes_path = import_path / 'votes.json'
    dates_path = import_path / 'dates.csv'

    for i in range(1960, 2025):
        cur.execute('INSERT OR IGNORE INTO year (id) VALUES (?)', (i,))

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

    with countries_path.open('r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            name = row['name']
            code = row['code']
            is_participating = int(row['is_participating'])
            cur.execute('INSERT OR IGNORE INTO country (name, id, is_participating) VALUES (?, ?, ?)', (name, code, is_participating))

    with alternatives_path.open('r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            code = row['code']
            name = row['name']
            from_year = int(row['from'])
            to_year = int(row['to'])

            cur.execute('''
                INSERT OR IGNORE INTO alternative_name (name, country_id, from_year_id, to_year_id)
                VALUES (?, ?, ?, ?)
            ''', (name, code, from_year, to_year))

    for f in specials_path.iterdir():
        continue
        if f.is_file() and f.suffix == '.csv':
            full_name, short_name, vote_options = f.stem.split('-')
            full_name = full_name.strip()
            short_name = short_name.strip()
            vote_options = int(vote_options.strip())

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
                    cur.execute('SELECT id FROM country WHERE name = ?', (country,))
                    country_id = cur.fetchone()[0]

                    title = row['title']
                    artist = row['artist']
                    cur.execute('''
                        INSERT INTO song (title, artist, submitter_id, country_id)
                        VALUES (?, ?, ?, ?)
                        RETURNING id
                    ''', (title, artist, user_id, country_id))
                    song_id = cur.fetchone()[0]

                    running_order = row['ro']
                    cur.execute('INSERT INTO song_show (song_id, show_id, running_order) VALUES (?, ?, ?)', (song_id, show_id, running_order))

    with regular_path.open('r', encoding='utf-16') as file:
        reader = csv.DictReader(file, delimiter='\t')
        for row in reader:
            username = row['Submitter']
            if username:
                username = username.strip()
                username = unicodedata.normalize('NFKC', username)
                cur.execute('INSERT OR IGNORE INTO user (username) VALUES (?)', (username,))
                cur.execute('SELECT id FROM user WHERE username = ?', (username,))
                user_id = cur.fetchone()[0]
            else:
                user_id = None

            country = row['Country']
            country = country.strip()
            cur.execute('SELECT id FROM country WHERE name = ?', (country,))
            country_id = cur.fetchone()[0]

            year = int(row['Year'])

            semifinal = None
            sf_show_full_name = None
            sf_show_short_name = None
            semifinal_raw = row['SFNo']
            sf_show_id = None
            if semifinal_raw:
                semifinal = int(semifinal_raw)
                if semifinal == 0:
                    sf_show_full_name = f"Semi-Final"
                    sf_show_short_name = f"sf"
                else:
                    sf_show_full_name = f"Semi-Final {semifinal}"
                    sf_show_short_name = f"sf{semifinal}"

                cur.execute('''
                    INSERT OR IGNORE INTO show (year_id, show_name, short_name, point_system_id)
                    VALUES (?, ?, ?, 1)
                ''',(year, sf_show_full_name, sf_show_short_name))
                cur.execute('SELECT id FROM show WHERE year_id = ? AND short_name = ?', (year, sf_show_short_name))
                sf_show_id = cur.fetchone()[0]

            semifinal_ro_raw = row['SFRO']
            semifinal_ro = None
            if semifinal_ro_raw:
                semifinal_ro = int(semifinal_ro_raw)

            final_ro_raw = row['FRO']
            final_ro = None
            final_show_id = None
            if final_ro_raw:
                final_ro = int(final_ro_raw)
                cur.execute('''
                    INSERT OR IGNORE INTO show (year_id, show_name, short_name, point_system_id)
                    VALUES (?, 'Final', 'f', 1)'''
                , (year,))
                cur.execute('''SELECT id FROM show WHERE year_id = ? AND short_name = 'f' ''', (year,))
                final_show_id = cur.fetchone()[0]

            title = row['Latin']
            title = title.strip()
            title = unicodedata.normalize('NFKC', title)
            native = row['Native']
            if native:
                native = native.strip()
                native = unicodedata.normalize('NFKC', native)
            else:
                native = None

            artist = row['Artist']
            artist = artist.strip()
            artist = unicodedata.normalize('NFKC', artist)

            is_placeholder = int(row['Placeholder'])

            cur.execute('''
                INSERT OR IGNORE INTO song (submitter_id, country_id, year_id, title, artist, native_title, is_placeholder)
                VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id
            ''', (user_id, country_id, year, title, artist, native, is_placeholder))
            song_id_raw = cur.fetchone()
            if not song_id_raw:
                continue
            song_id = song_id_raw[0]

            if sf_show_id:
                cur.execute('''
                    INSERT OR IGNORE INTO song_show (song_id, show_id, running_order)
                    VALUES (?, ?, ?)
                ''', (song_id, sf_show_id, semifinal_ro))

            if final_show_id:
                cur.execute('''
                    INSERT OR IGNORE INTO song_show (song_id, show_id, running_order)
                    VALUES (?, ?, ?)
                ''', (song_id, final_show_id, final_ro))

    with dates_path.open('r', encoding='utf-8') as file:
        reader = csv.reader(file)
        for row in reader:
            year = int(row[0])
            short_name = row[1]
            date = row[2]
            date = date.strip()

            cur.execute('SELECT id FROM show WHERE year_id = ? AND short_name = ?', (year, short_name))
            show_id = cur.fetchone()
            if not show_id:
                print(f"Show '{year} {short_name}' not found in DB, skipping date")
                continue
            show_id = show_id[0]
            date = datetime.date.fromisoformat(date)

            cur.execute('''
                UPDATE show
                SET date = ?
                WHERE id = ?
            ''', (date, show_id))

    with votes_path.open('r', encoding='utf-8') as file:
        votes = json.load(file)
        for year_txt, shows in votes.items():
            year = int(year_txt)
            for short_name, votes_data in shows.items():
                print(f"Processing votes for {year} {short_name}")
                cur.execute('SELECT id FROM show WHERE year_id = ? AND short_name = ?', (year, short_name))
                show_id = cur.fetchone()[0]
                for vote in votes_data:
                    country = vote['country'].strip()
                    cur.execute('SELECT id FROM country WHERE name = ?', (country,))
                    country_id = cur.fetchone()
                    if not country_id:
                        print(f"Country '{country}' not found in DB, skipping vote")
                        continue
                    else:
                        country_id = country_id[0]

                    voter_name = vote['voter']
                    voter_name = voter_name.strip()
                    voter_name = unicodedata.normalize('NFKC', voter_name)
                    cur.execute('INSERT OR IGNORE INTO user (username) VALUES (?)', (voter_name,))
                    cur.execute('SELECT id FROM user WHERE username = ?', (voter_name,))
                    voter_id = cur.fetchone()[0]

                    cur.execute('INSERT OR IGNORE INTO vote_set (voter_id, show_id, country_id) VALUES (?, ?, ?) RETURNING id', (voter_id, show_id, country_id))
                    vote_set_id = cur.fetchone()
                    if not vote_set_id:
                        print(f"Vote set for {voter_name} in {year} {short_name} already exists, skipping")
                        continue
                    else:
                        vote_set_id = vote_set_id[0]

                    for country, pts in vote['votes'].items():
                        cur.execute('SELECT id FROM country WHERE name = ?', (country,))
                        country_id = cur.fetchone()
                        if not country_id:
                            print(f"Country '{country}' not found in DB, skipping vote")
                            continue
                        else:
                            country_id = country_id[0]

                        cur.execute('SELECT id FROM song WHERE country_id = ? AND year_id = ?', (country_id, year))
                        song_id = cur.fetchone()
                        if not song_id:
                            print(f"Song for {country} in {year} not found in DB, skipping vote")
                            continue
                        else:
                            song_id = song_id[0]

                        cur.execute('SELECT id FROM point WHERE score = ? AND point_system_id = 1', (pts,))
                        point_id = cur.fetchone()[0]

                        cur.execute('''
                            INSERT OR IGNORE INTO vote (vote_set_id, song_id, point_id)
                            VALUES (?, ?, ?)
                        ''', (vote_set_id, song_id, point_id))

    cur.close()
    db.commit()

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

@click.command('populate-db')
def populate_db_command():
    """Populate the database with initial data."""
    populate_db()
    click.echo('Populated the database.')

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(populate_db_command)
    app.cli.add_command(migrate_db_command)

sqlite3.register_converter("datetime", lambda x: datetime.datetime.fromisoformat(x.decode('utf-8')).replace(tzinfo=datetime.timezone.utc))
sqlite3.register_converter("date", lambda x: datetime.date.fromisoformat(x.decode('utf-8')))