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
-- Optimized country voting analysis with reduced CTEs
WITH user_shows AS (
    SELECT DISTINCT show_id, country_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE voter_id = %(id)s
      AND s.access_type = 'full'
      AND s.year_id IS NOT NULL
),
-- Combine voting aggregations and song counts in one scan
voting_stats AS (
    SELECT
        s.country_id,
        SUM(CASE WHEN vs.voter_id = %(id)s THEN score ELSE 0 END) AS user_given,
        SUM(score) AS total_given,
        COUNT(DISTINCT CASE WHEN vs.voter_id = %(id)s THEN v.id END) AS user_votes,
        COUNT(DISTINCT CASE WHEN s.submitter_id <> %(id)s THEN ss.show_id || '-' || s.country_id END) AS show_country_pairs
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    LEFT JOIN vote_set vs ON vs.show_id = ss.show_id
    LEFT JOIN vote v ON v.vote_set_id = vs.id AND v.song_id = s.id
    WHERE s.submitter_id <> %(id)s
    GROUP BY s.country_id
),
-- Calculate entry counts per show-country for max points calculation
entry_counts AS (
    SELECT
        ss.show_id,
        s.country_id,
        COUNT(*) AS entry_cnt,
        sh.point_system_id
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN show sh ON sh.id = ss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> %(id)s
      AND sh.access_type = 'full'
      AND sh.year_id IS NOT NULL
    GROUP BY ss.show_id, s.country_id, sh.point_system_id
),
-- Calculate max points more efficiently
max_points_calc AS (
    SELECT
        ec.country_id,
        SUM(
            (SELECT SUM(score)
             FROM (
                SELECT score
                FROM point
                WHERE point_system_id = ec.point_system_id
                ORDER BY score DESC
                LIMIT ec.entry_cnt
             ) top_scores)
        ) AS max_pts_user,
        SUM(
            (SELECT SUM(score)
             FROM (
                SELECT score
                FROM point
                WHERE point_system_id = ec.point_system_id
                ORDER BY score DESC
                LIMIT ec.entry_cnt
             ) top_scores) * vc.voter_count
        ) AS max_pts_all
    FROM entry_counts ec
    JOIN (
        SELECT show_id, COUNT(*) AS voter_count
        FROM vote_set
        WHERE show_id IN (SELECT show_id FROM user_shows)
        GROUP BY show_id
    ) vc ON vc.show_id = ec.show_id
    GROUP BY ec.country_id
),
-- Calculate songs available per country
songs_available AS (
    SELECT
        s.country_id,
        COUNT(*) AS songs_available
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> %(id)s
    GROUP BY s.country_id
)
SELECT
    COALESCE(vs.country_id, mp.country_id, sa.country_id) AS country_id,
    c.name AS country_name,
    COALESCE(sa.songs_available, 0) AS parts,
    COALESCE(vs.user_given, 0) AS user_given,
    COALESCE(mp.max_pts_user, 0) AS user_max,
    CASE
        WHEN mp.max_pts_user > 0
        THEN COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user
        ELSE 0
    END AS user_ratio,
    COALESCE(vs.total_given, 0) AS total_given,
    COALESCE(mp.max_pts_all, 0) AS total_max,
    CASE
        WHEN mp.max_pts_all > 0
        THEN COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all
        ELSE 0
    END AS total_ratio,
    CASE
        WHEN mp.max_pts_all > 0 AND vs.total_given > 0 AND mp.max_pts_user > 0
        THEN ((COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user) /
              (COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all)) - 1
        ELSE 0
    END AS bias,
    CASE
        WHEN COALESCE(sa.songs_available, 0) < 5 THEN 'inconclusive'
        WHEN CASE
                WHEN mp.max_pts_all > 0 AND vs.total_given > 0 AND mp.max_pts_user > 0
                THEN ((COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user) /
                      (COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all)) - 1
                ELSE 0
             END < -0.5 THEN 'very-negative'
        WHEN CASE
                WHEN mp.max_pts_all > 0 AND vs.total_given > 0 AND mp.max_pts_user > 0
                THEN ((COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user) /
                      (COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all)) - 1
                ELSE 0
             END < -0.1 THEN 'negative'
        WHEN CASE
                WHEN mp.max_pts_all > 0 AND vs.total_given > 0 AND mp.max_pts_user > 0
                THEN ((COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user) /
                      (COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all)) - 1
                ELSE 0
             END < 0.1 THEN 'neutral'
        WHEN CASE
                WHEN mp.max_pts_all > 0 AND vs.total_given > 0 AND mp.max_pts_user > 0
                THEN ((COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user) /
                      (COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all)) - 1
                ELSE 0
             END < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS bias_class
FROM voting_stats vs
FULL OUTER JOIN max_points_calc mp ON mp.country_id = vs.country_id
FULL OUTER JOIN songs_available sa ON sa.country_id = COALESCE(vs.country_id, mp.country_id)
LEFT JOIN country c ON c.id = COALESCE(vs.country_id, mp.country_id, sa.country_id)
WHERE COALESCE(sa.songs_available, 0) > 0
ORDER BY
    CASE
        WHEN mp.max_pts_all > 0 AND vs.total_given > 0 AND mp.max_pts_user > 0
        THEN ((COALESCE(vs.user_given, 0)::numeric / mp.max_pts_user) /
              (COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all)) - 1
        ELSE 0
    END DESC,
    COALESCE(sa.songs_available, 0),
    CASE
        WHEN mp.max_pts_all > 0
        THEN COALESCE(vs.total_given, 0)::numeric / mp.max_pts_all
        ELSE 0
    END
    ''', {'id': user_id})

    for r in cursor.fetchall():
        yield dict(r)

def get_submitter_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
WITH user_shows AS (
    SELECT DISTINCT show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE voter_id = %(id)s
      AND s.access_type = 'full'
),
-- Points given TO targets (other submitters)
voting_to_targets AS (
    SELECT
        s.submitter_id AS target_id,
        SUM(CASE WHEN vs.voter_id = %(id)s THEN score ELSE 0 END) AS pts_user_to_target,
        SUM(score) AS pts_all_to_target,
        COUNT(DISTINCT s.id) AS songs_available
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    WHERE vs.show_id IN (SELECT show_id FROM user_shows)
      AND s.submitter_id <> %(id)s
    GROUP BY s.submitter_id
),
voting_from_targets AS (
    SELECT
        vs.voter_id AS target_id,
        SUM(score) AS pts_target_to_user
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id = %(id)s
      AND vs.voter_id <> %(id)s
    GROUP BY vs.voter_id
),
max_points_calc AS (
    SELECT
        sc.target_id,
        SUM(
            (SELECT SUM(score)
             FROM (
                SELECT score
                FROM point
                WHERE point_system_id = sh.point_system_id
                ORDER BY score DESC
                LIMIT sc.entry_cnt
             ) top_scores)
        ) AS max_pts_user,
        SUM(
            (SELECT SUM(score)
             FROM (
                SELECT score
                FROM point
                WHERE point_system_id = sh.point_system_id
                ORDER BY score DESC
                LIMIT sc.entry_cnt
             ) top_scores) * vc.voter_count
        ) AS max_pts_all
    FROM (
        SELECT
            ss.show_id,
            s.submitter_id AS target_id,
            COUNT(*) AS entry_cnt
        FROM song_show ss
        JOIN song s ON s.id = ss.song_id
        WHERE ss.show_id IN (SELECT show_id FROM user_shows)
          AND s.submitter_id <> %(id)s
        GROUP BY ss.show_id, s.submitter_id
    ) sc
    JOIN show sh ON sh.id = sc.show_id
    JOIN (
        SELECT show_id, COUNT(*) AS voter_count
        FROM vote_set
        WHERE show_id IN (SELECT show_id FROM user_shows)
        GROUP BY show_id
    ) vc ON vc.show_id = sc.show_id
    GROUP BY sc.target_id
)
SELECT
    COALESCE(vt.target_id, vf.target_id, mp.target_id) AS submitter_id,
    u.username AS submitter_name,
    COALESCE(vt.songs_available, 0) AS parts,
    COALESCE(vt.pts_user_to_target, 0) AS user_given,
    COALESCE(vf.pts_target_to_user, 0) AS submitter_given,
    COALESCE(vt.pts_all_to_target, 0) AS total_given,
    COALESCE(vt.pts_user_to_target, 0) - COALESCE(vf.pts_target_to_user, 0) AS points_deficit,
    COALESCE(mp.max_pts_user, 0) AS user_max,
    COALESCE(mp.max_pts_all, 0) AS all_max,
    CASE
        WHEN mp.max_pts_user > 0
        THEN COALESCE(vt.pts_user_to_target, 0)::numeric / mp.max_pts_user
        ELSE 0
    END AS user_ratio,
    CASE
        WHEN mp.max_pts_all > 0
        THEN COALESCE(vt.pts_all_to_target, 0)::numeric / mp.max_pts_all
        ELSE 0
    END AS total_ratio,
    CASE
        WHEN vf.pts_target_to_user > 0
        THEN (COALESCE(vt.pts_user_to_target, 0)::numeric / vf.pts_target_to_user) - 1
        ELSE 0
    END AS reciprocal_bias,
    CASE
        WHEN mp.max_pts_all > 0 AND vt.pts_all_to_target > 0
        THEN ((COALESCE(vt.pts_user_to_target, 0)::numeric / mp.max_pts_user) /
              (COALESCE(vt.pts_all_to_target, 0)::numeric / mp.max_pts_all)) - 1
        ELSE 0
    END AS bias,
    CASE
        WHEN COALESCE(vt.songs_available, 0) < 5 THEN 'inconclusive'
        WHEN ((COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(mp.max_pts_user, 0)) /
              NULLIF((COALESCE(vt.pts_all_to_target, 0)::numeric / NULLIF(mp.max_pts_all, 0)), 0)) - 1 < -0.5 THEN 'very-negative'
        WHEN ((COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(mp.max_pts_user, 0)) /
              NULLIF((COALESCE(vt.pts_all_to_target, 0)::numeric / NULLIF(mp.max_pts_all, 0)), 0)) - 1 < -0.1 THEN 'negative'
        WHEN ((COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(mp.max_pts_user, 0)) /
              NULLIF((COALESCE(vt.pts_all_to_target, 0)::numeric / NULLIF(mp.max_pts_all, 0)), 0)) - 1 < 0.1 THEN 'neutral'
        WHEN ((COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(mp.max_pts_user, 0)) /
              NULLIF((COALESCE(vt.pts_all_to_target, 0)::numeric / NULLIF(mp.max_pts_all, 0)), 0)) - 1 < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS bias_class,
    CASE
        WHEN COALESCE(vt.songs_available, 0) < 5 THEN 'inconclusive'
        WHEN (COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(vf.pts_target_to_user, 0)) - 1 < -0.5 THEN 'very-negative'
        WHEN (COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(vf.pts_target_to_user, 0)) - 1 < -0.1 THEN 'negative'
        WHEN (COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(vf.pts_target_to_user, 0)) - 1 < 0.1 THEN 'neutral'
        WHEN (COALESCE(vt.pts_user_to_target, 0)::numeric / NULLIF(vf.pts_target_to_user, 0)) - 1 < 0.5 THEN 'positive'
        ELSE 'very-positive'
    END AS reciprocal_bias_class
FROM voting_to_targets vt
FULL OUTER JOIN voting_from_targets vf ON vf.target_id = vt.target_id
FULL OUTER JOIN max_points_calc mp ON mp.target_id = COALESCE(vt.target_id, vf.target_id)
LEFT JOIN account u ON u.id = COALESCE(vt.target_id, vf.target_id, mp.target_id)
WHERE COALESCE(vt.songs_available, 0) > 0
ORDER BY
    CASE
        WHEN mp.max_pts_all > 0 AND vt.pts_all_to_target > 0
        THEN ((COALESCE(vt.pts_user_to_target, 0)::numeric / mp.max_pts_user) /
              (COALESCE(vt.pts_all_to_target, 0)::numeric / mp.max_pts_all)) - 1
        ELSE 0
    END DESC,
    COALESCE(vt.songs_available, 0),
    CASE
        WHEN mp.max_pts_all > 0
        THEN COALESCE(vt.pts_all_to_target, 0)::numeric / mp.max_pts_all
        ELSE 0
    END
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