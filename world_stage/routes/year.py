from collections import defaultdict
from flask import request, Blueprint
import typing

from ..utils import (LCG, Show, SuspensefulVoteSequencer,
                     get_show_id, dt_now, get_user_role_from_session,
                     get_votes_for_song, get_year_songs, get_year_winner,
                     get_special_winner, render_template, get_show_songs)
from ..db import get_db

bp = Blueprint('year', __name__, url_prefix='/year')

def get_specials() -> list[dict]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute('SELECT short_name, show_name, date FROM show WHERE year_id IS NULL')
    specials = [row for row in cursor.fetchall()]

    for special in specials:
        special['winner'] = get_special_winner(special['short_name'])

    return specials

def get_other_shows(year: int | None, exclude_show: str | None) -> list[str]:
    db = get_db()
    cursor = db.cursor()

    if year:
        cursor.execute('''
            SELECT short_name FROM show
            WHERE year_id = %s AND short_name <> %s
            ORDER BY id
        ''', (year, exclude_show))
    else:
        cursor.execute('''
            SELECT short_name FROM show
            WHERE year_id IS NULL AND short_name <> %s
            ORDER BY id
        ''', (exclude_show,))

    return [row['short_name'] for row in cursor.fetchall()]

@bp.get('/')
def index():
    db = get_db()
    cursor = db.cursor()

    years = []
    upcoming = []
    ongoing = []

    cursor.execute('SELECT id, closed FROM year ORDER BY id DESC')
    for data in cursor.fetchall():
        if data['closed'] == 1:
            years.append(data)
        elif data['closed'] == 2:
            ongoing.append(data)
        else:
            upcoming.append(data)

    upcoming.reverse()

    for year in years:
        year['winner'] = get_year_winner(year['id'])

    specials = get_specials()

    return render_template('year/index.html', years=years, upcoming=upcoming, specials=specials, ongoing=ongoing)

@bp.get('/<year>')
def year(year: str):
    db = get_db()
    cursor = db.cursor()

    try:
        _year = int(year)
    except ValueError:
        _year = None

    if _year:
        cursor.execute('SELECT closed FROM year WHERE id = %s', (year,))
        closed = cursor.fetchone()
        if not closed:
            return render_template('error.html', error='Year not closed yet'), 404

        songs = get_year_songs(_year, select_languages=True)

        cursor.execute('SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder', (_year,))
        total_entries = cursor.fetchone()['c'] # type: ignore
        total_placeholders = len(songs)-total_entries
        cursor.execute('SELECT short_name, show_name, date FROM show WHERE year_id = %s', (year,))
        shows = [Show(year=_year, short_name=show['short_name'], name=show['show_name'], date=show['date']) for show in cursor.fetchall()]
        shows.sort()

        return render_template('year/year.html', year=year, songs=songs,
                               closed=closed['closed'], shows=shows, total=total_entries, placeholders=total_placeholders)
    else:
        return render_template('year/specials.html', specials=get_specials())

@bp.get('/<year>/<show>')
def results(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type == 'none' and not permissions.can_view_restricted:
        return render_template('error.html', error="This show has no songs"), 400

    reveal = ''
    access = show_data.access_type

    if permissions.can_view_restricted:
        if access == 'draw':
            access = 'partial'
            reveal = "unrevealed"
        elif access == 'partial':
            access = 'full'
            reveal = "unrevealed"
        else:
            access = 'full'

    if access == 'draw':
        songs = get_show_songs(_year, show, select_votes=False)
    else:
        songs = get_show_songs(_year, show, select_votes=True)

    if not songs:
        return render_template('error.html', error="No songs found for this show."), 404

    participants = len(songs)

    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT COUNT(voter_id) AS c FROM vote_set WHERE show_id = %s', (show_data.id,))
    voter_count = cursor.fetchone()['c'] # type: ignore
    songs.sort(reverse=True)

    off = 0
    if access == 'partial':
        if show_data.dtf:
            off = show_data.dtf - 1
        if show_data.sc:
            off += show_data.sc
        songs = songs[off:]
        if reveal:
            for s in songs:
                s.hidden = True

        if songs:
            if songs[0].vote_data:
                songs[0].vote_data.ro = -1
            songs[0].artist = ''
            songs[0].title = ''
            songs[0].country.name = ''
            songs[0].country.cc = 'XXX'
    elif access == 'full' and reveal:
        if show_data.dtf:
            off = show_data.dtf - 1
        if show_data.sc:
            off += show_data.sc
        if reveal:
            for i in range(off + 1):
                songs[i].hidden = True
        off = 0

    qualifiers = show_data.dtf or 0
    sc_qualifiers = (show_data.sc or 0) + (show_data.special or 0) + qualifiers

    return render_template('year/summary.html', hidden=reveal, qualifiers=qualifiers, sc_qualifiers=sc_qualifiers,
                           songs=songs, points=show_data.points, show=show, access=access, offset=off, other_shows=get_other_shows(_year, show),
                           show_name=show_data.name, short_name=show_data.short_name, show_id=show_data.id, year=year, participants=participants, voters=voter_count)

@bp.get('/<year>/<show>/detailed')
def detailed_results(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type != 'full' and not permissions.can_view_restricted:
        return render_template('error.html', error="You aren't allowed to access the detailed results yet"), 400

    if (show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted):
        return render_template('error.html', error="Voting hasn't closed yet."), 400

    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template('error.html', error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    results: dict = {}
    cursor.execute('''
        SELECT username, COALESCE(country_id, 'XXX') as code, country.name AS country FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        LEFT OUTER JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
        ORDER BY created_at
    ''', (show_data.id,))
    for row in cursor.fetchall():
        results[row['username']] = row

    for song in songs:
        cursor.execute('''
            SELECT point.score, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN account ON vote_set.voter_id = account.id
            JOIN point ON vote.point_id = point.id
            WHERE song_id = %s AND show_id = %s
            ORDER BY created_at
        ''', (song.id, show_data.id))

        for row in cursor.fetchall():
            results[row['username']][song.id] = row['score']

    songs.sort(reverse=True)

    qualifiers = show_data.dtf or 0
    sc_qualifiers = (show_data.sc or 0) + (show_data.special or 0) + qualifiers

    return render_template('year/detailed.html', qualifiers=qualifiers, sc_qualifiers=sc_qualifiers, other_shows=get_other_shows(_year, show),
                           songs=songs, results=results, show_name=show_data.name, show=show, year=year, participants=len(songs))

@bp.get('/<year>/<show>/scoreboard')
def scoreboard(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type != 'full' and not permissions.can_view_restricted:
        return render_template('error.html', error="You aren't allowed to access the scoreboard yet"), 400

    if (show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted):
        return render_template('error.html', error="Voting hasn't closed yet."), 400

    return render_template('year/scoreboard.html', show=show, year=year, show_name=show_data.name)

@bp.get('/<year>/<show>/scoreboard/votes')
def scores(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type != 'full' and not permissions.can_view_restricted:
        return {'error': "You aren't allowed to access the scoreboard"}, 400

    if (show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted):
        return {'error': "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return {"error": "No songs found for this show."}, 404

    cursor.execute('''
        SELECT song_id, point.score AS pts, username FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        JOIN song ON vote.song_id = song.id
        JOIN point ON vote.point_id = point.id
        WHERE vote_set.show_id = %s
        ORDER BY vote_set.created_at
    ''', (show_data.id,))
    results_raw = cursor.fetchall()
    results: dict[str, dict[int, int]] = defaultdict(dict)
    for row in results_raw:
        results[row['username']][row['pts']] = row['song_id']

    sequencer = SuspensefulVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    vote_order = sequencer.get_order()

    user_songs = defaultdict(list)
    for voter_username in vote_order:
        cursor.execute('''
            SELECT song.id FROM song
            JOIN account ON song.submitter_id = account.id
            JOIN song_show ON song.id = song_show.song_id
            WHERE account.username = %s AND song_show.show_id = %s
        ''', (voter_username,show_data.id))
        for song_id in cursor.fetchall():
            user_songs[voter_username].append(song_id['id'])

    cursor.execute('''
        SELECT username, nickname, country_id AS code, country.name AS country FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN country ON vote_set.country_id = country.id
        WHERE vote_set.show_id = %s
    ''', (show_data.id,))
    vote_set = cursor.fetchall()
    voter_assoc = {}
    for row in vote_set:
        voter_assoc[row['username']] = row

    return {'songs': songs, 'results': results, 'points': show_data.points, 'vote_order': vote_order, 'associations': voter_assoc, 'user_songs': user_songs}

@bp.get('/<year>/<show>/qualifiers')
def qualifiers(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

    if show_data.dtf is None:
        return render_template('error.html', error="Not a semi-final."), 400

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type != 'full' and not permissions.can_view_restricted:
        return render_template('error.html', error="You aren't allowed to access the qualifiers")

    if (show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted):
        return render_template('error.html', error="Voting hasn't closed yet."), 400

    return render_template('year/qualifiers.html', show=show, year=year, show_name=show_data.name)

@bp.post('/<year>/<show>/qualifiers')
def qualifiers_post(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type != 'full' and not permissions.can_view_restricted:
        return {'error': "You aren't allowed to access the qualifiers"}, 400

    final_data = get_show_id('f', _year)
    if not final_data:
        return {"error": "Final show not found"}, 404

    sc_data = get_show_id('sc', _year)

    if show_data.short_name == 'sc':
        sf_number = 9
    elif show_data.short_name.startswith('sf'):
        sf_number = int(show_data.short_name.removeprefix('sf'))
    else:
        return {'error': "Invalid semi-final show name"}, 400

    body = request.json
    if not body or not isinstance(body, dict):
        return {'error': "Invalid request body"}, 400

    action = body.get('action')
    if action != 'save':
        return {'error': "Invalid action"}, 400

    db = get_db()
    cursor = db.cursor()

    final_order = body.get('dtf')
    if not final_order or not isinstance(final_order, list):
        return {'error': "Reveal order not provided"}, 400

    for i, song_id in enumerate(final_order):
        n = sf_number + (i + 1) / 100
        if sf_number == 9:
            add = 20
        else:
            add = 1
        cursor.execute('''
            INSERT INTO song_show (song_id, show_id, running_order, qualifier_order)
            VALUES (%(soid)s, %(shid)s, %(ro)s, %(qo)s)
            ON CONFLICT (show_id, song_id) DO UPDATE
            SET song_id = %(soid)s,
                show_id = %(shid)s,
                running_order = %(ro)s,
                qualifier_order = %(qo)s
        ''', {'soid': int(song_id), 'shid': final_data.id, 'ro': n, 'qo': i + add})

    if sc_data:
        second_chance_order = typing.cast(list[int], body.get('sc'))
        if second_chance_order and not isinstance(second_chance_order, list):
            return {'error': "Second chance order must be a list"}, 400

        for i, song_id in enumerate(second_chance_order):
            n = sf_number + (i + 1) / 100
            cursor.execute('''
                INSERT INTO song_show (song_id, show_id, running_order, qualifier_order)
                VALUES (%(soid)s, %(shid)s, %(ro)s, %(qo)s)
                ON CONFLICT (show_id, song_id) DO UPDATE
                SET song_id = %(soid)s,
                    show_id = %(shid)s,
                    running_order = %(ro)s,
                    qualifier_order = %(qo)s
        ''', {'soid': int(song_id), 'shid': sc_data.id, 'ro': n, 'qo': i + 1})
    db.commit()

    return {'success': True, 'message': "Qualifiers saved successfully."}

@bp.get('/<year>/<show>/qualifiers/votes')
def qualifiers_scores(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return {"error": "Show not found"}, 404

    if show_data.dtf is None:
        return {"error": "Not a semi-final."}, 400

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if show_data.access_type != 'full' and not permissions.can_view_restricted:
        return {'error': "You aren't allowed to access the qualifiers"}, 400

    if (show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted):
        return {'error': "Voting hasn't closed yet."}, 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT song.id, song_show.running_order, country.name AS country, country.id AS cc FROM song
        JOIN song_show ON song.id = song_show.song_id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY song_show.running_order
    ''', (show_data.id,))
    countries = []
    for row in cursor.fetchall():
        val = {
            'id': row['id'],
            'country': row['country'],
            'cc': row['cc'],
            'points': get_votes_for_song(row['id'], show_data.id, row['running_order'])
        }
        countries.append(val)

    countries.sort(key=lambda x: x['points'], reverse=True)

    dtf_countries = []
    for i in range(show_data.dtf):
        dtf_countries.append(countries[i])

    sc_countries = []
    for i in range(show_data.sc or 0):
        sc_countries.append(countries[show_data.dtf + i])

    countries.sort(key=lambda x: x['points'].ro)

    for c in countries:
        del c['points']

    lcg = LCG(show_data.id)
    lcg.shuffle(dtf_countries)
    lcg.shuffle(sc_countries)

    return {'countries': countries,'reveal_order': {'dtf': dtf_countries, 'sc': sc_countries},
            'dtf': show_data.dtf, 'sc': show_data.sc or 0, 'special': show_data.special or 0}