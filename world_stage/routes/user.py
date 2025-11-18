from collections import defaultdict
import urllib.parse
import unicodedata
from flask import Blueprint, request

from ..utils import get_user_songs, render_template
from ..db import get_db

bp = Blueprint('user', __name__, url_prefix='/user')

@bp.get('/')
def index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id, username, role FROM account
        ORDER BY username
    ''')
    users = defaultdict(list)
    users['Admin'] = []
    for row in cursor.fetchall():
        if row['role'] == 'admin' or row['role'] == 'owner':
            users['Admin'].append({
                'id': row['id'],
                'username': row['username']
            })
        first_letter = row['username'][0].upper()
        val = {
            'id': row['id'],
            'username': row['username']
        }
        users[first_letter].append(val)

    return render_template('user/index.html', users=users)

@bp.get('/<username>')
def profile(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    return render_template('user/page.html', username=username)

def redact_song_if_show(song: dict, year: int, show_short_name: str, access_type: str) -> tuple[bool, bool]:
    db = get_db()
    cursor = db.cursor()
    show_exists = False
    song_modified = False

    cursor.execute('''
        SELECT id FROM show WHERE year_id = %s AND short_name = %s
    ''', (year, show_short_name))
    show = cursor.fetchone()
    if show:
        show_exists = True
        cursor.execute('''
            SELECT COUNT(*) AS c FROM song_show
            WHERE show_id = %s AND song_id = %s
        ''', (show['id'], song['id']))
        if cursor.fetchone()['c'] > 0: # type: ignore
            song_modified = True
            song['class'] = f'qualifier {show_short_name}-qualifier'
            if access_type == 'partial':
                    song['title'] = ''
                    song['artist'] = ''
                    song['country'] = ''
                    song['code'] = 'XXX'

    return (show_exists, song_modified)


@bp.get('/<username>/votes')
def votes(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM account WHERE username = %s
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id['id']

    cursor.execute('''
        SELECT vote_set.id, account.username, nickname, country_id, show.show_name, show.short_name, show.date, show.year_id, show.access_type FROM vote_set
        JOIN account ON vote_set.voter_id = account.id
        JOIN show ON vote_set.show_id = show.id
        WHERE vote_set.voter_id = %s AND (show.access_type = 'full' OR show.access_type = 'partial')
        ORDER BY show.date DESC
    ''', (user_id,))
    votes = []
    for row in cursor.fetchall():
        val = {
            'id': row['id'],
            'username': row['username'],
            'nickname': row['nickname'] or username,
            'code': row['country_id'],
            'show_name': row['show_name'],
            'short_name': row['short_name'],
            'access_type': row['access_type'],
            'date': row['date'].strftime("%d %b %Y"),
            'year': row['year_id']
        }
        votes.append(val)

    for vote in votes:
        cursor.execute('''
            SELECT score AS pts, song.title, song.artist, song.country_id AS code, country.name, song.id FROM vote
            JOIN song ON vote.song_id = song.id
            JOIN country ON song.country_id = country.id
            WHERE vote.vote_set_id = %s
            ORDER BY score DESC
        ''', (vote['id'],))
        songs = []
        for val in cursor.fetchall():
            if vote['short_name'] != 'f':
                redact_song_if_show(val, vote['year'], 'f', vote['access_type'])
                if vote['short_name'] != 'sc':
                    redact_song_if_show(val, vote['year'], 'sc', vote['access_type'])
            songs.append(val)

        vote['points'] = songs

    return render_template('user/votes.html', votes=votes, username=username)

@bp.get('/<username>/submissions')
def submissions(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM account WHERE username = %s
    ''', (username,))
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id_g['id']

    songs = get_user_songs(user_id, select_languages=True)

    return render_template('user/submissions.html', songs=songs, username=username)

def get_country_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
WITH user_shows AS (
    SELECT DISTINCT s.id AS show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE vs.voter_id = %(id)s
      AND s.access_type = 'full'
      AND s.year_id IS NOT NULL
),
songs_available AS (
    SELECT
        s.country_id,
        COUNT(*) AS songs_available
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    JOIN show sh ON sh.id = ss.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
      AND sh.year_id IS NOT NULL
    GROUP BY s.country_id
),
all_votes AS (
    SELECT
        s.country_id,
        SUM(v.score) AS total_given
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
      AND sh.year_id IS NOT NULL
    GROUP BY s.country_id
),
all_totals AS (
    SELECT COALESCE(SUM(total_given), 0) AS total_given_all
    FROM all_votes
),
show_user_points AS (
    SELECT
        vs.show_id,
        SUM(v.score) AS user_points_in_show
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    WHERE vs.voter_id = %(id)s
      AND vs.show_id IN (SELECT show_id FROM user_shows)
    GROUP BY vs.show_id
),
country_shows AS (
    SELECT DISTINCT
        ss.show_id,
        s.country_id
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    JOIN show sh ON sh.id = ss.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
      AND sh.year_id IS NOT NULL
),
user_exposure AS (
    SELECT
        cs.country_id,
        COALESCE(SUM(sup.user_points_in_show), 0) AS exposure_points
    FROM country_shows cs
    LEFT JOIN show_user_points sup ON sup.show_id = cs.show_id
    GROUP BY cs.country_id
),
user_votes AS (
    SELECT
        s.country_id,
        SUM(v.score) AS user_given,
        COUNT(DISTINCT v.id) AS user_votes
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
      AND sh.year_id IS NOT NULL
      AND vs.voter_id = %(id)s
    GROUP BY s.country_id
),
user_total AS (
    SELECT
        COALESCE(SUM(user_given), 0) AS user_total_given,
        COALESCE(SUM(user_votes), 0) AS user_vote_events
    FROM user_votes
),
combined AS (
    SELECT
        COALESCE(av.country_id, uv.country_id, sa.country_id, ue.country_id) AS country_id,
        COALESCE(sa.songs_available, 0) AS songs_available,
        COALESCE(uv.user_given, 0) AS user_given,
        COALESCE(uv.user_votes, 0) AS user_votes,
        COALESCE(av.total_given, 0) AS total_given,
        COALESCE(ue.exposure_points, 0) AS exposure_points
    FROM all_votes av
    FULL OUTER JOIN user_votes uv
        ON uv.country_id = av.country_id
    FULL OUTER JOIN songs_available sa
        ON sa.country_id = COALESCE(av.country_id, uv.country_id)
    FULL OUTER JOIN user_exposure ue
        ON ue.country_id = COALESCE(av.country_id, uv.country_id, sa.country_id)
),
bias_values AS (
    SELECT
        ctry.country_id,
        ctry.songs_available,
        ctry.user_given,
        ctry.user_votes,
        ctry.total_given,
        ctry.exposure_points,
        ut.user_total_given::numeric AS user_total_given,
        at.total_given_all::numeric AS total_given_all,
        CASE
            WHEN ut.user_total_given > 0
            THEN ctry.user_given::numeric / ut.user_total_given
            ELSE 0
        END AS q_c,
        CASE
            WHEN at.total_given_all > 0
            THEN ctry.total_given::numeric / at.total_given_all
            ELSE 0
        END AS p_c
    FROM combined ctry
    CROSS JOIN user_total ut
    CROSS JOIN all_totals at
),
scored AS (
    SELECT
        bv.country_id,
        bv.songs_available,
        bv.user_given,
        bv.user_votes,
        bv.total_given,
        bv.exposure_points,
        bv.user_total_given,
        bv.total_given_all,
        bv.q_c,
        bv.p_c,
        bv.exposure_points::numeric AS N_c,
        100.0::numeric AS s0,
        CASE
            WHEN bv.p_c > 0 AND bv.exposure_points > 0
            THEN (bv.exposure_points / (bv.exposure_points + 100.0)) * bv.q_c
               + (100.0 / (bv.exposure_points + 100.0)) * bv.p_c
            WHEN bv.p_c > 0
            THEN bv.p_c
            ELSE 0
        END AS q_hat
    FROM bias_values bv
),
final_bias AS (
    SELECT
        s.*,
        CASE
            WHEN s.p_c > 0
            THEN (s.q_hat - s.p_c) / (s.p_c + 0.0005)
            ELSE 0
        END AS bias
    FROM scored s
),
classified AS (
    SELECT
        fb.*,
        CASE
            WHEN fb.songs_available < 5 OR fb.N_c < 50
                THEN 'inconclusive'
            WHEN fb.bias < -0.5
                THEN 'very-negative'
            WHEN fb.bias < -0.1
                THEN 'negative'
            WHEN fb.bias < 0.1
                THEN 'neutral'
            WHEN fb.bias < 0.5
                THEN 'positive'
            ELSE 'very-positive'
        END AS bias_class
    FROM final_bias fb
)
SELECT
    ctry.country_id,
    c.name AS country_name,
    ctry.songs_available AS parts,
    ctry.user_given,
    ctry.user_total_given AS user_max,
    ctry.q_c AS user_ratio,
    ctry.total_given,
    ctry.total_given_all AS total_max,
    ctry.p_c AS total_ratio,
    ctry.bias,
    ctry.bias_class
FROM classified ctry
LEFT JOIN country c ON c.id = ctry.country_id
WHERE ctry.songs_available > 0
ORDER BY
    ctry.bias DESC,
    ctry.songs_available,
    ctry.p_c
    ''', {'id': user_id})

    for r in cursor.fetchall():
        yield dict(r)

def get_submitter_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
WITH user_shows AS (
    SELECT DISTINCT s.id AS show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE vs.voter_id = %(id)s
      AND s.access_type = 'full'
),
songs_available AS (
    SELECT
        s.submitter_id AS submitter_id,
        COUNT(DISTINCT s.id) AS songs_available
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    JOIN show sh ON sh.id = ss.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
    GROUP BY s.submitter_id
),
all_votes AS (
    SELECT
        s.submitter_id AS submitter_id,
        SUM(v.score) AS total_given
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
    GROUP BY s.submitter_id
),
all_totals AS (
    SELECT COALESCE(SUM(total_given), 0) AS total_given_all
    FROM all_votes
),
show_user_points AS (
    SELECT
        vs.show_id,
        SUM(v.score) AS user_points_in_show
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN show sh ON sh.id = vs.show_id
    WHERE vs.voter_id = %(id)s
      AND sh.access_type = 'full'
      AND vs.show_id IN (SELECT show_id FROM user_shows)
    GROUP BY vs.show_id
),
submitter_shows AS (
    SELECT DISTINCT
        ss.show_id,
        s.submitter_id
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    JOIN show sh ON sh.id = ss.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
),
user_exposure AS (
    SELECT
        ss.submitter_id,
        COALESCE(SUM(sup.user_points_in_show), 0) AS exposure_points
    FROM submitter_shows ss
    LEFT JOIN show_user_points sup ON sup.show_id = ss.show_id
    GROUP BY ss.submitter_id
),
user_votes AS (
    SELECT
        s.submitter_id AS submitter_id,
        SUM(v.score) AS user_given,
        COUNT(DISTINCT v.id) AS user_votes
    FROM user_shows us
    JOIN vote_set vs ON vs.show_id = us.show_id
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
      AND vs.voter_id = %(id)s
    GROUP BY s.submitter_id
),
user_total AS (
    SELECT
        COALESCE(SUM(user_given), 0) AS user_total_given,
        COALESCE(SUM(user_votes), 0) AS user_vote_events
    FROM user_votes
),
combined AS (
    SELECT
        COALESCE(av.submitter_id, uv.submitter_id, sa.submitter_id, ue.submitter_id) AS submitter_id,
        COALESCE(sa.songs_available, 0) AS songs_available,
        COALESCE(uv.user_given, 0) AS user_given,
        COALESCE(uv.user_votes, 0) AS user_votes,
        COALESCE(av.total_given, 0) AS total_given,
        COALESCE(ue.exposure_points, 0) AS exposure_points
    FROM all_votes av
    FULL OUTER JOIN user_votes uv
        ON uv.submitter_id = av.submitter_id
    FULL OUTER JOIN songs_available sa
        ON sa.submitter_id = COALESCE(av.submitter_id, uv.submitter_id)
    FULL OUTER JOIN user_exposure ue
        ON ue.submitter_id = COALESCE(av.submitter_id, uv.submitter_id, sa.submitter_id)
),
bias_values AS (
    SELECT
        ctry.submitter_id,
        ctry.songs_available,
        ctry.user_given,
        ctry.user_votes,
        ctry.total_given,
        ctry.exposure_points,
        ut.user_total_given::numeric AS user_total_given,
        at.total_given_all::numeric AS total_given_all,
        CASE
            WHEN ut.user_total_given > 0
            THEN ctry.user_given::numeric / ut.user_total_given
            ELSE 0
        END AS q_s,
        CASE
            WHEN at.total_given_all > 0
            THEN ctry.total_given::numeric / at.total_given_all
            ELSE 0
        END AS p_s
    FROM combined ctry
    CROSS JOIN user_total ut
    CROSS JOIN all_totals at
),
scored AS (
    SELECT
        bv.submitter_id,
        bv.songs_available,
        bv.user_given,
        bv.user_votes,
        bv.total_given,
        bv.exposure_points,
        bv.user_total_given,
        bv.total_given_all,
        bv.q_s,
        bv.p_s,
        bv.exposure_points::numeric AS N_s,
        100.0::numeric AS s0,
        CASE
            WHEN bv.p_s > 0 AND bv.exposure_points > 0
            THEN (bv.exposure_points / (bv.exposure_points + 100.0)) * bv.q_s
               + (100.0 / (bv.exposure_points + 100.0)) * bv.p_s
            WHEN bv.p_s > 0
            THEN bv.p_s
            ELSE 0
        END AS q_hat
    FROM bias_values bv
),
final_bias AS (
    SELECT
        s.*,
        CASE
            WHEN s.p_s > 0
            THEN (s.q_hat - s.p_s) / (s.p_s + 0.0005)
            ELSE 0
        END AS bias
    FROM scored s
),
reciprocal_points AS (
    SELECT
        vs.voter_id AS submitter_id,
        SUM(v.score) AS pts_target_to_user
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    JOIN show sh ON sh.id = vs.show_id
    WHERE s.submitter_id = %(id)s
      AND vs.voter_id <> %(id)s
      AND sh.access_type = 'full'
    GROUP BY vs.voter_id
),
classified AS (
    SELECT
        fb.*,
        rp.pts_target_to_user,
        CASE
            WHEN fb.songs_available < 5 OR fb.N_s < 50
                THEN 'inconclusive'
            WHEN fb.bias < -0.5
                THEN 'very-negative'
            WHEN fb.bias < -0.1
                THEN 'negative'
            WHEN fb.bias < 0.1
                THEN 'neutral'
            WHEN fb.bias < 0.5
                THEN 'positive'
            ELSE 'very-positive'
        END AS bias_class,
        CASE
            WHEN fb.songs_available < 5 OR rp.pts_target_to_user IS NULL OR rp.pts_target_to_user = 0
                THEN 'inconclusive'
            WHEN (fb.user_given::numeric / rp.pts_target_to_user) - 1 < -0.5
                THEN 'very-negative'
            WHEN (fb.user_given::numeric / rp.pts_target_to_user) - 1 < -0.1
                THEN 'negative'
            WHEN (fb.user_given::numeric / rp.pts_target_to_user) - 1 < 0.1
                THEN 'neutral'
            WHEN (fb.user_given::numeric / rp.pts_target_to_user) - 1 < 0.5
                THEN 'positive'
            ELSE 'very-positive'
        END AS reciprocal_bias_class,
        CASE
            WHEN rp.pts_target_to_user > 0
            THEN (fb.user_given::numeric / rp.pts_target_to_user) - 1
            ELSE 0
        END AS reciprocal_bias
    FROM final_bias fb
    LEFT JOIN reciprocal_points rp ON rp.submitter_id = fb.submitter_id
)
SELECT
    ctry.submitter_id,
    a.username AS submitter_name,
    ctry.songs_available AS parts,
    ctry.user_given,
    COALESCE(ctry.pts_target_to_user, 0) AS submitter_given,
    ctry.total_given,
    (ctry.user_given - COALESCE(ctry.pts_target_to_user, 0)) AS points_deficit,
    ctry.user_total_given AS user_max,
    ctry.total_given_all AS all_max,
    ctry.q_s AS user_ratio,
    ctry.p_s AS total_ratio,
    ctry.reciprocal_bias,
    ctry.bias,
    ctry.bias_class,
    ctry.reciprocal_bias_class
FROM classified ctry
LEFT JOIN account a ON a.id = ctry.submitter_id
WHERE ctry.songs_available > 0
ORDER BY
    ctry.bias DESC,
    ctry.songs_available,
    ctry.p_s
    ''', {'id': user_id})

    for r in cursor.fetchall():
        yield dict(r)

@bp.get('/<username>/bias')
def bias(username: str):
    username = urllib.parse.unquote(username)
    username = unicodedata.normalize('NFKC', username)

    bias_type = request.args.get('type', 'country')

    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
        SELECT id FROM account WHERE username = %s
    ''', (username,))
    user_id_g = cursor.fetchone()
    if not user_id_g:
        return {'error': 'User not found'}, 404
    user_id = user_id_g['id']

    if bias_type == 'user':
        biases = get_submitter_biases(user_id)
    elif bias_type == 'country':
        biases = get_country_biases(user_id)
    else:
        return render_template('error.html', error=f"Invalid bias type specified: {bias_type}."), 400

    return render_template('user/bias.html', username=username, bias_type=bias_type, biases=biases)