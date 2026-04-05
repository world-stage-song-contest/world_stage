from collections import defaultdict, Counter
import io
from flask import Response, request, Blueprint
import typing
import math

from ..utils import (LCG, AbstractVoteSequencer, RandomVoteSequencer, Show, SuspensefulVoteSequencer,
                     ChronologicalVoteSequencer, ShowData,
                     get_show_id, dt_now, get_user_role_from_session,
                     get_votes_for_song, get_year_songs, get_year_placements, get_year_winner,
                     get_show_results_for_songs, get_special_winner, render_template, get_show_songs)
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
        closed = cursor.fetchone() or {'closed': 0}
        cl = closed['closed'] == 1

        songs = get_year_songs(_year, select_languages=True)

        free_countries = []

        if closed['closed'] == 0:
            cursor.execute('''
                SELECT id, name FROM country
                WHERE id <> ALL(%(ccs)s)
                  AND is_participating = true
                  AND available_from <= %(year)s
                  AND available_until >= %(year)s
                ORDER BY name
            ''', {'ccs': [s.country.cc for s in songs], 'year': _year})

            free_countries = cursor.fetchall()

        cursor.execute('SELECT COUNT(*) AS c FROM song WHERE year_id = %s AND NOT is_placeholder', (_year,))
        total_entries = cursor.fetchone()['c'] # type: ignore
        total_placeholders = len(songs)-total_entries
        cursor.execute('SELECT short_name, show_name, date FROM show WHERE year_id = %s ORDER BY id', (year,))
        shows = [Show(year=_year, short_name=show['short_name'], name=show['show_name'], date=show['date']) for show in cursor.fetchall()]
        shows.sort()

        year_placements = get_year_placements(_year) if cl else {}

        show_names = {s.short_name for s in shows}
        has_sc = 'sc' in show_names
        has_sf = any(sn == 'sf' or sn.startswith('sf') for sn in show_names)
        multi_show = has_sc or has_sf

        results = get_show_results_for_songs([s.id for s in songs]) if (multi_show and cl == 1) else {}

        # SF assignment: which semi-final each song competed in, regardless of
        # whether the show is published yet (no access_type gate).
        sf_numbers: dict[int, str] = {}
        if has_sf:
            cursor.execute('''
                SELECT ss.song_id, sh.short_name
                FROM song_show ss
                JOIN show sh ON sh.id = ss.show_id
                WHERE sh.year_id = %s
                  AND LEFT(sh.short_name, 2) = 'sf'
            ''', (_year,))
            sf_numbers = {row['song_id']: row['short_name'] for row in cursor.fetchall()}

        return render_template('year/year.html', year=year, songs=songs, free_countries=free_countries,
                               is_closed=cl, shows=shows, total=total_entries, placeholders=total_placeholders,
                               year_placements=year_placements, results=results,
                               multi_show=multi_show, has_sc=has_sc, has_sf=has_sf,
                               sf_numbers=sf_numbers)
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
            SELECT score, username FROM vote
            JOIN vote_set ON vote.vote_set_id = vote_set.id
            JOIN account ON vote_set.voter_id = account.id
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
        SELECT song_id, score AS pts, username FROM vote
        JOIN vote_set ON vote.vote_set_id = vote_set.id
        JOIN account ON vote_set.voter_id = account.id
        JOIN song ON vote.song_id = song.id
        WHERE vote_set.show_id = %s
        ORDER BY vote_set.created_at
    ''', (show_data.id,))
    results_raw = cursor.fetchall()
    results: dict[str, dict[int, int]] = defaultdict(dict)
    for row in results_raw:
        results[row['username']][row['pts']] = row['song_id']

    sequencer: AbstractVoteSequencer
    if show_data.id < 60:
        sequencer = SuspensefulVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    elif show_data.id < 65:
        sequencer = RandomVoteSequencer(results, songs, show_data.points, seed=show_data.id)
    else:
        sequencer = ChronologicalVoteSequencer(results, songs, show_data.points, seed=show_data.id)
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
        n = sf_number * 100 + (i + 1)
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
            n = sf_number * 100 + (i + 1)
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

def _compute_prediction_odds(
    songs: list,
    pred_by_set: dict,
    n_predictors: int,
    n_qualifiers: int,
) -> dict[int, float]:
    """
    Compute qualifying probability for each song from prediction data.

    Based on the World Stage predictions spreadsheet formula:
    1. Ranking points per predictor: 12 * 0.827^(position - 1)
    2. Qualifier flag per predictor: 1 if predicted position <= n_qualifiers else 0
    3. Combined raw score using place percentage, points percentage and qualifier fraction
    4. Normalised so that sum of all probabilities = n_qualifiers

    For finals (n_qualifiers == 0) every song is treated as a potential qualifier
    and results are normalised so they sum to n_songs (average probability = 1.0).

    Returns a dict of {song_id: probability} where sum(values) == effective_n_qualifiers.
    """
    n_songs = len(songs)
    if n_predictors == 0 or n_songs == 0:
        return {song.id: 0.0 for song in songs}

    # For shows without an explicit qualifier count (e.g. grand finals) treat
    # all songs as eligible so the normalisation denominator is meaningful.
    effective_n_qual = n_qualifiers if n_qualifiers > 0 else n_songs

    raw: dict[int, float] = {}
    for song in songs:
        positions = [set_preds.get(song.id, n_songs) for set_preds in pred_by_set.values()]

        # PlacePct: 1 = predicted 1st, 0 = predicted last
        avg_rank = sum(positions) / n_predictors
        place_pct = (1 - (avg_rank - 1) / (n_songs - 1)) if n_songs > 1 else 1.0

        # PtsPct: average ranking points normalised by (n_predictors * 12)
        avg_pts = sum(12 * (0.827 ** (p - 1)) for p in positions) / n_predictors
        pts_pct = avg_pts / (n_predictors * 12)

        # QPct: AVERAGE(1, qual_flag_1, …) — always positive, biased toward qualifying
        qual_flags = [1 if pos <= effective_n_qual else 0 for pos in positions]
        q_pct = (1 + sum(qual_flags)) / (1 + n_predictors)

        # QBonus: extra weight based on whether the average prediction is a qualifier
        is_avg_qual = avg_rank <= effective_n_qual
        q_bonus = 0.125 * (2 * place_pct if is_avg_qual else 8 * pts_pct)

        # Raw score (A4 = 0.1 is the spreadsheet baseline constant)
        raw_val = (0.1 + place_pct * pts_pct) * (q_pct + 0.05) + q_bonus
        res = (0.9 + raw_val * 0.04) if raw_val > 0.9 else (raw_val + 0.01)
        raw[song.id] = res

    total = sum(raw.values())
    if total == 0:
        return {song.id: 0.0 for song in songs}

    # Normalise with iterative capping so no probability exceeds 1.0.
    # Any song whose scaled value would exceed 1.0 is fixed at 1.0 and removed
    # from the pool; its excess budget is redistributed among the rest.
    # Repeat until all remaining values are ≤ 1.0.
    unfixed: dict[int, float] = dict(raw)
    result: dict[int, float] = {}
    remaining = float(effective_n_qual)

    while unfixed:
        pool_total = sum(unfixed.values())
        if pool_total <= 0:
            result.update({k: 0.0 for k in unfixed})
            break

        scale = remaining / pool_total
        scaled = {k: v * scale for k, v in unfixed.items()}

        over = {k for k, v in scaled.items() if v >= 1.0}
        if not over:
            result.update(scaled)
            break

        for k in over:
            result[k] = 1.0
        remaining -= len(over)
        unfixed = {k: raw[k] for k in unfixed if k not in over}

        if remaining <= 0:
            result.update({k: 0.0 for k in unfixed})
            break

    return result


@bp.get('/<year>/<show>/predictions')
def show_predictions(year: str, show: str):
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
        return render_template('error.html', error="You aren't allowed to access the predictions yet"), 400

    if (show_data.voting_closes
            and show_data.voting_closes > dt_now()
            and not permissions.can_view_restricted):
        return render_template('error.html', error="Voting hasn't closed yet."), 400

    # select_votes=True populates song.vote_data, which carries the running order
    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template('error.html', error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT prediction_set.id, account.username, prediction_set.created_at
        FROM prediction_set
        JOIN account ON prediction_set.user_id = account.id
        WHERE prediction_set.show_id = %s
        ORDER BY prediction_set.created_at
    ''', (show_data.id,))
    pred_sets = cursor.fetchall()

    cursor.execute('''
        SELECT prediction.set_id, prediction.song_id, prediction.position
        FROM prediction
        JOIN prediction_set ON prediction.set_id = prediction_set.id
        WHERE prediction_set.show_id = %s
    ''', (show_data.id,))

    pred_by_set: dict[int, dict[int, int]] = defaultdict(dict)
    for row in cursor.fetchall():
        pred_by_set[row['set_id']][row['song_id']] = row['position']

    n_predictors = len(pred_sets)
    n_qualifiers = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)

    odds = _compute_prediction_odds(songs, pred_by_set, n_predictors, n_qualifiers)

    # Apply a per-rank soft cap so no entry can show 100% odds.
    # epsilon is in (0, 0.005] and is consistent per show via LCG.
    # Rank 1 cap = 0.98 + ε, rank 2 = 0.98, rank 3 = 0.98 − ε, …
    if n_predictors > 0:
        lcg = LCG(show_data.id)
        epsilon = 0.0001 + (lcg.next(None) / (2 ** 32)) * 0.0049  # (0.0001, 0.005)
        for rank, sid in enumerate(sorted(odds, key=lambda s: odds[s], reverse=True), 1):
            cap = 0.98 + epsilon * (2 - rank)
            if cap > 0:
                odds[sid] = min(odds[sid], cap)

    # Build predictor dict ordered by submission time: {username: {song_id: position}}
    predictors: dict[str, dict] = {}
    for ps in pred_sets:
        predictors[ps['username']] = pred_by_set.get(ps['id'], {})

    # Sort songs by qualifying probability descending (mirrors detailed sort by total points)
    songs.sort(key=lambda s: odds[s.id], reverse=True)

    # Pre-render copyable odds text
    copy_lines: list[str] = []
    for i, song in enumerate(songs, 1):
        prob = odds[song.id]
        decimal_odds = (1 / prob) if prob > 0 else float('inf')
        pct = prob * 100
        copy_lines.append(f"{i}. {song.country.name}: {decimal_odds:.2f} ({pct:.2f}%)")
    copy_text = '\n'.join(copy_lines)

    # Copy box is an admin tool — hide it when the page is publicly visible
    show_copy = show_data.access_type != 'full'

    return render_template('year/predictions.html',
                           songs=songs, predictors=predictors, odds=odds,
                           n_predictors=n_predictors, n_qualifiers=n_qualifiers,
                           copy_text=copy_text, show_copy=show_copy,
                           show=show, show_name=show_data.name, year=year,
                           other_shows=get_other_shows(_year, show))


@bp.get('/<year>/<show>/voters')
def show_voters(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if not permissions.can_view_restricted:
        return render_template('error.html', error="You aren't allowed to access this show"), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT username, nickname, COALESCE(country.id, 'XXX') FROM vote_set
        JOIN account ON voter_id = account.id
        LEFT OUTER JOIN country ON country_id = country.id
        WHERE show_id = %s
    ''', (show_data.id,))

    return render_template('year/voters.html')

def generate_playlist(show_data: ShowData, postcards: bool) -> tuple[str, list[str]]:
    def write(buf: io.StringIO, val: str):
        buf.write(val)
        buf.write("\n")

    def write_header(buf: io.StringIO):
        write(buf, "#EXTINF:0")
        write(buf, "#EXTVLCOPT:network-caching=3000")

    def write_country(buf: io.StringIO, cc: str, url: str) -> str | None:
        if postcards:
            write_header(buf)
            write(buf, f"https://media.world-stage.org/postcards/{cc.lower()}.mov")

        write_header(buf)
        v = None
        if 'media.world-stage.org' not in url:
            v = cc

        write(buf, url or 'BAD LINK REPLACE ME THIS IS A BUG')

        return v

    def show_needs_host(show_data: ShowData) -> bool:
        if show_data.access_type != 'draw':
            return False

        if not show_data.short_name.startswith('sf'):
            return False

        sn = int(show_data.short_name[2])
        if sn % 2 == 0:
            return False

        return True

    db = get_db()
    cursor = db.cursor()

    insert_after = -1
    host = ''
    host_link = ''
    if show_needs_host(show_data):
        cursor.execute('''
            SELECT LOWER(cc2) AS cc2, video_link FROM year
            JOIN country ON year.host = country.id
            JOIN song ON song.country_id = year.host
            WHERE year.id = %(y)s AND song.year_id = %(y)s
        ''', {'y': show_data.year})
        data = cursor.fetchone()
        if data:
            cursor.execute('''
                SELECT COUNT(id) AS c FROM song_show
                WHERE show_id = %s
            ''', (show_data.id,))
            insert_after = math.ceil(cursor.fetchone()['c'] / 2) - 1 # type: ignore
            host = data.get('cc2') or ''
            host_link = data.get('video_link') or ''

    cursor.execute('''
        SELECT cc2, video_link FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY running_order
    ''', (show_data.id,))

    output = io.StringIO(newline='\r\n')
    output.write("#EXTM3U\n")

    bad_countries = []

    for i, song in enumerate(cursor.fetchall()):
        cc = song.get('cc2') or ''
        url = song.get('video_link') or ''
        b = write_country(output, cc, url)
        if b is not None:
            bad_countries.append(b)

        if i == insert_after:
            write_country(output, host, host_link)

    write_header(output)
    write(output, f"https://media.world-stage.org/recaps/{show_data.year}{show_data.short_name}.mov")

    return output.getvalue(), bad_countries

@bp.get('/<year>/<show>/playlist')
def show_playlist(year: str, show: str):
    try:
        _year = int(year)
    except ValueError:
        _year = None
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template('error.html', error="Show not found"), 404

    postcards = request.args.get('postcards', 'false') == 'true'

    value, bad_countries = generate_playlist(show_data, postcards)

    session_id = request.cookies.get('session')
    permissions = get_user_role_from_session(session_id)

    if permissions.can_view_restricted:
        bad_countries = []

    if bad_countries:
        bad_countries.sort()
        return render_template('error.html', error=("Not all links for this show have been corrected. "
                                                    "Please ping one of the admins. "
                                                    f"Invalid links: {', '.join(bad_countries)}."))

    if postcards:
        extra = ""
    else:
        extra = "x"

    filename = f"{year}{show}{extra}.m3u"

    response = Response(
        value,
        mimetype='audio/x-mpegurl',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )
    return response