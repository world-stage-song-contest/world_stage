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
        SELECT id, username, role FROM user
        ORDER BY username
    ''')
    users = defaultdict(list)
    users['Admin'] = []
    for id, username, role in cursor.fetchall():
        if role == 'admin' or role == 'owner':
            users['Admin'].append({
                'id': id,
                'username': username
            })
        first_letter = username[0].upper()
        val = {
            'id': id,
            'username': username
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
        SELECT id FROM show WHERE year_id = ? AND short_name = ?
    ''', (year, show_short_name))
    show = cursor.fetchone()
    if show:
        show_exists = True
        cursor.execute('''
            SELECT COUNT(*) FROM song_show
            WHERE show_id = ? AND song_id = ?
        ''', (show[0], song['id']))
        if cursor.fetchone()[0] > 0:
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
        SELECT id FROM user WHERE username = ?
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id[0]

    cursor.execute('''
        SELECT vote_set.id, user.username, nickname, country_id, show.show_name, show.short_name, show.date, show.year_id, show.allow_access_type FROM vote_set
        JOIN user ON vote_set.voter_id = user.id
        JOIN show ON vote_set.show_id = show.id
        WHERE vote_set.voter_id = ? AND (show.allow_access_type = 'full' OR show.allow_access_type = 'partial')
        ORDER BY show.date DESC
    ''', (user_id,))
    votes = []
    for id, username, nickname, country_id, show_name, short_name, date, year, access_type in cursor.fetchall():
        val = {
            'id': id,
            'username': username,
            'nickname': nickname or username,
            'code': country_id,
            'show_name': show_name,
            'short_name': short_name,
            'access_type': access_type,
            'date': date.strftime("%d %b %Y"),
            'year': year
        }
        votes.append(val)

    for vote in votes:
        cursor.execute('''
            SELECT point.score, song.title, song.artist, song.country_id, country.name, song.id FROM vote
            JOIN song ON vote.song_id = song.id
            JOIN point ON vote.point_id = point.id
            JOIN country ON song.country_id = country.id
            WHERE vote.vote_set_id = ?
            ORDER BY point.score DESC
        ''', (vote['id'],))
        songs = []
        for pts, title, artist, country_id, country, id in cursor.fetchall():
            val = {
                'id': id,
                'pts': pts,
                'title': title,
                'artist': artist,
                'code': country_id,
                'country': country,
                'class': ''
            }

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
        SELECT id FROM user WHERE username = ?
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return render_template('error.html', error="User not found"), 404
    user_id = user_id[0]

    songs = get_user_songs(user_id, select_languages=True)

    return render_template('user/submissions.html', songs=songs, username=username)

def get_country_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
WITH
user_shows AS (
    SELECT DISTINCT show_id,
                    country_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE voter_id = ?1 AND s.allow_access_type = 'full'
),
song_counts AS (
    SELECT ss.show_id,
           s.country_id,
           COUNT(*) AS entry_cnt
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN show sh ON sh.id = ss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> ?1 AND sh.allow_access_type = 'full'
    GROUP BY ss.show_id,
             s.country_id
),
ranked_points AS (
    SELECT sh.id AS show_id,
           p.score,
           ROW_NUMBER() OVER (PARTITION BY sh.id
                               ORDER BY p.score DESC) AS rn
    FROM user_shows us
    JOIN SHOW sh ON sh.id = us.show_id
    JOIN POINT p ON p.point_system_id = sh.point_system_id
),
max_pts_1_voter AS (
    SELECT sc.show_id,
           sc.country_id,
           SUM(rp.score) AS max_pts_single_voter
    FROM song_counts sc
    JOIN ranked_points rp ON rp.show_id = sc.show_id
    AND rp.rn <= sc.entry_cnt
    GROUP BY sc.show_id,
            sc.country_id
),
given_user AS (
    SELECT s.country_id,
           SUM(p.score) AS given_points
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN POINT p ON p.id = v.point_id
    JOIN song s ON s.id = v.song_id
    WHERE vs.voter_id = ?1
      AND s.submitter_id <> ?1
    GROUP BY s.country_id
),
given_all AS (
    SELECT s.country_id,
           SUM(p.score) AS given_points
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN POINT p ON p.id = v.point_id
    JOIN song s ON s.id = v.song_id
    WHERE vs.show_id IN
            (SELECT show_id
            FROM user_shows)
      AND s.submitter_id <> ?1
    GROUP BY s.country_id
),
max_total_user AS (
    SELECT country_id,
           SUM(max_pts_single_voter) AS max_points
    FROM max_pts_1_voter
    GROUP BY country_id
),
voter_cnt AS (
    SELECT show_id,
           COUNT(*) AS n
    FROM vote_set
    WHERE show_id IN
            (SELECT show_id
            FROM user_shows)
    GROUP BY show_id
),
max_total_all AS (
    SELECT m.show_id,
           m.country_id,
           m.max_pts_single_voter * vc.n AS pts_all_voters
    FROM max_pts_1_voter m
    JOIN voter_cnt vc USING (show_id)
),
max_total_all_sum AS (
    SELECT country_id,
           SUM(pts_all_voters) AS max_points
    FROM max_total_all
    GROUP BY country_id
),
country_ids AS (
    SELECT country_id
    FROM given_user
    UNION SELECT country_id
    FROM given_all
    UNION SELECT country_id
    FROM max_total_user
    UNION SELECT country_id
    FROM max_total_all_sum
),
final AS (
    SELECT ci.country_id AS country_id,
           COALESCE(gu.given_points, 0) AS user_given,
           COALESCE(mu.max_points, 0) AS user_max,
           COALESCE(ga.given_points, 0) AS total_given,
           COALESCE(ma.max_points, 0) AS total_max
    FROM country_ids ci
    LEFT JOIN given_user gu ON gu.country_id = ci.country_id
    LEFT JOIN max_total_user mu ON mu.country_id = ci.country_id
    LEFT JOIN given_all ga ON ga.country_id = ci.country_id
    LEFT JOIN max_total_all_sum ma ON ma.country_id = ci.country_id
),
ratios AS (
    SELECT f.country_id,
           f.user_given,
           f.user_max,
           COALESCE(f.user_given*1.0 / f.user_max, 0.0) AS user_ratio,
           f.total_given,
           f.total_max,
           COALESCE(f.total_given *1.0 / f.total_max, 0.0) AS total_ratio,
           COALESCE((f.user_given*1.0/f.user_max) / (f.total_given *1.0/f.total_max), 0) - 1 AS bias
    FROM final f
    LEFT JOIN country c ON c.id = f.country_id
)
SELECT r.country_id,
       c.name AS country_name,
       COALESCE(sc.songs_available, 0) AS participations,
       r.user_given,
       r.user_max,
       r.user_ratio,
       r.total_given,
       r.total_max,
       r.total_ratio,
       r.bias,
       CASE
           WHEN COALESCE(sc.songs_available, 0) < 5 THEN 'inconclusive'
           WHEN r.bias < -0.5 THEN 'very-negative'
           WHEN r.bias < -0.1 THEN 'negative'
           WHEN r.bias < 0.1 THEN 'neutral'
           WHEN r.bias < 0.5 THEN 'positive'
           ELSE 'very-positive'
       END AS bias_class
FROM ratios r
LEFT JOIN country c ON c.id = r.country_id
LEFT JOIN
    (SELECT country_id,
            SUM(entry_cnt) AS songs_available
    FROM song_counts GROUP  BY country_id) sc ON sc.country_id = r.country_id
ORDER  BY bias DESC, participations, r.total_ratio
    ''', (user_id,))

    for r in cursor.fetchall():
        yield dict(r)

def get_submitter_biases(user_id: int):
    db = get_db()
    cursor = db.cursor()

    cursor.execute('''
WITH
user_shows AS (
    SELECT DISTINCT show_id
    FROM vote_set vs
    JOIN show s ON s.id = vs.show_id
    WHERE voter_id = ?1 AND s.allow_access_type = 'full'
),
song_counts AS (
    SELECT ss.show_id,
           s.submitter_id AS target_id,
           COUNT(*) AS entry_cnt
    FROM user_shows us
    JOIN song_show ss ON ss.show_id = us.show_id
    JOIN show sh ON sh.id = ss.show_id
    JOIN song s ON s.id = ss.song_id
    WHERE s.submitter_id <> ?1 AND sh.allow_access_type = 'full'
    GROUP BY ss.show_id,
             s.submitter_id
),
ranked_points AS (
    SELECT sh.id AS show_id,
           p.score,
           ROW_NUMBER() OVER (PARTITION BY sh.id
                              ORDER BY p.score DESC) AS rn
    FROM user_shows us
    JOIN SHOW sh ON sh.id = us.show_id
    JOIN POINT p ON p.point_system_id = sh.point_system_id
),
max_pts_1_voter AS (
    SELECT sc.show_id,
           sc.target_id,
           SUM(rp.score) AS max_pts_single_voter
    FROM song_counts sc
    JOIN ranked_points rp ON rp.show_id = sc.show_id
    AND rp.rn <= sc.entry_cnt
    GROUP BY sc.show_id,
             sc.target_id
),
given_user AS (
    SELECT s.submitter_id AS target_id,
           SUM(p.score) AS given_points
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN POINT p ON p.id = v.point_id
    JOIN song s ON s.id = v.song_id
    WHERE vs.voter_id = ?1
      AND s.submitter_id <> ?1
    GROUP BY s.submitter_id
),
given_all AS (
    SELECT s.submitter_id AS target_id,
           SUM(p.score) AS given_points
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN POINT p ON p.id = v.point_id
    JOIN song s ON s.id = v.song_id
    WHERE vs.show_id IN
            (SELECT show_id
             FROM user_shows)
      AND s.submitter_id <> ?1
    GROUP BY s.submitter_id
),
given_back AS (
    SELECT vs.voter_id AS target_id,
           SUM(p.score) AS points_back
    FROM vote_set vs
    JOIN vote v ON v.vote_set_id = vs.id
    JOIN POINT p ON p.id = v.point_id
    JOIN song s ON s.id = v.song_id
    WHERE s.submitter_id = ?1
      AND vs.voter_id <> ?1
     GROUP BY vs.voter_id
),
max_total_user AS (
    SELECT target_id,
           SUM(max_pts_single_voter) AS max_points
    FROM max_pts_1_voter
    GROUP BY target_id
),
voter_cnt AS (
    SELECT show_id,
           COUNT(*) AS n
    FROM vote_set
    WHERE show_id IN
            (SELECT show_id
             FROM user_shows)
    GROUP BY show_id
),
max_total_all AS (
    SELECT m.show_id,
           m.target_id,
           m.max_pts_single_voter * vc.n AS pts_all_voters
    FROM max_pts_1_voter m
    JOIN voter_cnt vc USING (show_id)
),
max_total_all_sum AS (
    SELECT target_id,
           SUM(pts_all_voters) AS max_points
    FROM max_total_all
    GROUP BY target_id
),
target_ids AS (
    SELECT target_id
    FROM given_user
    UNION SELECT target_id
    FROM given_all
    UNION SELECT target_id
    FROM given_back
    UNION SELECT target_id
    FROM max_total_user
    UNION SELECT target_id
    FROM max_total_all_sum
),
final AS (
    SELECT ti.target_id,
           COALESCE(gu.given_points, 0) AS pts_user_to_target,
           COALESCE(mu.max_points , 0) AS user_max,
           COALESCE(ga.given_points, 0) AS pts_all_to_target,
           COALESCE(ma.max_points , 0) AS all_max,
           COALESCE(gb.points_back , 0) AS pts_target_to_user
    FROM target_ids ti
    LEFT JOIN given_user gu ON gu.target_id = ti.target_id
    LEFT JOIN max_total_user mu ON mu.target_id = ti.target_id
    LEFT JOIN given_all ga ON ga.target_id = ti.target_id
    LEFT JOIN max_total_all_sum ma ON ma.target_id = ti.target_id
    LEFT JOIN given_back gb ON gb.target_id = ti.target_id
),
participations AS (
    SELECT s.submitter_id AS target_id,
           COUNT(*) AS songs_available
    FROM song s
    JOIN song_show ss ON ss.song_id = s.id
    WHERE ss.show_id IN
            (SELECT show_id
             FROM user_shows)
      AND s.submitter_id <> ?1
    GROUP BY s.submitter_id
),
ratios AS (
    SELECT f.target_id AS target_id,
           f.pts_user_to_target AS points_user_to_submitter,
           f.pts_target_to_user AS points_submitter_to_user,
           f.pts_all_to_target AS points_all_to_submitter,
           f.user_max,
           f.all_max,
           COALESCE(f.pts_user_to_target*1.0 / f.user_max, 0) AS user_ratio,
           COALESCE(f.pts_all_to_target*1.0 / f.all_max, 0) AS total_ratio,
           COALESCE(f.pts_user_to_target*1.0 / f.pts_target_to_user, 0) - 1 AS reciprocal_bias
    FROM final f
    LEFT JOIN user u ON u.id = f.target_id
    LEFT JOIN participations p ON p.target_id = f.target_id
),
biases AS (
    SELECT r.target_id,
           COALESCE((r.user_ratio / r.total_ratio), 0) - 1 AS total_bias
    FROM ratios r
)
SELECT f.target_id AS submitter_id,
       u.username AS submitter_name,
       COALESCE(p.songs_available, 0) AS participations,
       f.pts_user_to_target AS user_given,
       f.pts_target_to_user AS submitter_given,
       f.pts_all_to_target AS total_given,
       f.pts_user_to_target - f.pts_target_to_user AS points_deficit,
       f.user_max,
       f.all_max,
       r.user_ratio AS user_ratio,
       r.total_ratio AS total_ratio,
       r.reciprocal_bias AS reciprocal_bias,
       b.total_bias AS bias,
       CASE
           WHEN COALESCE(p.songs_available, 0) < 5 THEN 'inconclusive'
           WHEN b.total_bias < -0.5 THEN 'very-negative'
           WHEN b.total_bias < -0.1 THEN 'negative'
           WHEN b.total_bias < 0.1 THEN 'neutral'
           WHEN b.total_bias < 0.5 THEN 'positive'
           ELSE 'very-positive'
       END AS bias_class,
       CASE
           WHEN COALESCE(p.songs_available, 0) < 5 THEN 'inconclusive'
           WHEN r.reciprocal_bias < -0.5 THEN 'very-negative'
           WHEN r.reciprocal_bias < -0.1 THEN 'negative'
           WHEN r.reciprocal_bias < 0.1 THEN 'neutral'
           WHEN r.reciprocal_bias < 0.5 THEN 'positive'
           ELSE 'very-positive'
       END AS reciprocal_bias_class
FROM FINAL f
LEFT JOIN user u ON u.id = f.target_id
LEFT JOIN biases b ON b.target_id = f.target_id
LEFT JOIN ratios r ON r.target_id = f.target_id
LEFT JOIN participations p ON p.target_id = f.target_id
WHERE participations > 0
ORDER BY bias DESC, participations, total_ratio
    ''', (user_id,))

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
        SELECT id FROM user WHERE username = ?
    ''', (username,))
    user_id = cursor.fetchone()
    if not user_id:
        return {'error': 'User not found'}, 404
    user_id = user_id[0]

    if bias_type == 'user':
        biases = get_submitter_biases(user_id)
    elif bias_type == 'country':
        biases = get_country_biases(user_id)
    else:
        return render_template('error.html', error=f"Invalid bias type specified: {bias_type}."), 400

    return render_template('user/bias.html', username=username, bias_type=bias_type, biases=biases)