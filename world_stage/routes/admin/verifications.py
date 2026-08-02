from collections import defaultdict

from flask import redirect, request, url_for

from ...db import get_db
from ...utils import render_template, require_user
from ...utils.song_revisions import set_song_status
from .common import _resolve_special, bp

NOTIFICATION_STATUSES = {"rejected", "more-info"}
MAX_NOTIFICATION_LENGTH = 5000


def _verification_subject(song: dict, status: str) -> str:
    action = "Song submission rejected" if status == "rejected" else "More information requested"
    artist = song["artist"] or "Unknown artist"
    title = song["title"] or "Untitled"
    return f"{action}: {artist} – {title}"[:200]


def _notify_submitter(
    cursor,
    song: dict,
    status: str,
    moderator_id: int,
    body: str,
) -> None:
    submitter_id = song["submitter_id"]
    if submitter_id is None:
        return

    cursor.execute(
        """
        INSERT INTO conversation (
            subject, metadata, admin_accessible, created_by_admin
        )
        VALUES (
            %s,
            jsonb_build_object(
                'banner', true,
                'submitter_id', %s::bigint
            ),
            true,
            true
        )
        RETURNING id
        """,
        (_verification_subject(song, status), submitter_id),
    )
    conversation_id = cursor.fetchone()["id"]
    cursor.execute(
        """
        INSERT INTO conversation_participant (
            conversation_id, account_id, role
        ) VALUES (%s, %s, 'owner')
        """,
        (conversation_id, moderator_id),
    )
    if submitter_id != moderator_id:
        cursor.execute(
            """
            INSERT INTO conversation_participant (
                conversation_id, account_id, role
            ) VALUES (%s, %s, 'participant')
            """,
            (conversation_id, submitter_id),
        )
    cursor.execute(
        """
        INSERT INTO message (conversation_id, sender_id, sender_kind, body)
        VALUES (%s, %s, 'admin', %s)
        """,
        (conversation_id, moderator_id, body),
    )


def _render_verifications(year: dict):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT data.id, song.id AS song_id, data.country_id, data.entry_number,
               data.artist, data.title, data.sources,
               COALESCE(status.is_placeholder, false) AS is_placeholder,
               COALESCE(status.approval_status, 'pending') AS approval_status,
               status.id AS status_id,
               status.created_at AS status_created_at,
               data.created_at,
               country.name AS country_name,
               submitter.username AS submitter_username,
               latest.id AS latest_data_id,
               song.id IS NULL OR latest.title IS NULL OR latest.artist IS NULL
                   AS song_deleted
        FROM song_data AS data
        LEFT JOIN song
          ON song.country_id = data.country_id
         AND song.year_id = data.year_id
         AND song.entry_number IS NOT DISTINCT FROM data.entry_number
        JOIN LATERAL (
            SELECT newest.id, newest.title, newest.artist
            FROM song_data AS newest
            WHERE newest.country_id = data.country_id
              AND newest.year_id = data.year_id
              AND newest.entry_number IS NOT DISTINCT FROM data.entry_number
            ORDER BY newest.created_at DESC, newest.id DESC
            LIMIT 1
        ) AS latest ON true
        JOIN LATERAL (
            SELECT newest.id
            FROM song_data AS newest
            WHERE newest.country_id = data.country_id
              AND newest.year_id = data.year_id
              AND newest.entry_number IS NOT DISTINCT FROM data.entry_number
              AND newest.title IS NOT NULL
              AND newest.artist IS NOT NULL
            ORDER BY newest.created_at DESC, newest.id DESC
            LIMIT 1
        ) AS latest_content ON true
        LEFT JOIN LATERAL (
            SELECT newer.id, newer.title, newer.artist
            FROM song_data AS newer
            WHERE newer.country_id = data.country_id
              AND newer.year_id = data.year_id
              AND newer.entry_number IS NOT DISTINCT FROM data.entry_number
              AND (newer.created_at, newer.id) > (data.created_at, data.id)
            ORDER BY newer.created_at, newer.id
            LIMIT 1
        ) AS next_data ON true
        LEFT JOIN LATERAL (
            SELECT record.id, record.approval_status, record.is_placeholder,
                   record.created_at
            FROM song_status AS record
            WHERE record.song_id = song.id
            ORDER BY record.created_at DESC, record.id DESC
            LIMIT 1
        ) AS status ON true
        JOIN country ON country.id = data.country_id
        LEFT JOIN account AS submitter ON submitter.id = data.submitter_id
        LEFT JOIN song_revision_merge AS manual_merge
          ON manual_merge.song_data_id = data.id
        LEFT JOIN song_verification_hidden_revision AS hidden_revision
          ON hidden_revision.song_data_id = data.id
        WHERE data.year_id = %s
          AND data.title IS NOT NULL
          AND data.artist IS NOT NULL
          AND manual_merge.song_data_id IS NULL
          AND hidden_revision.song_data_id IS NULL
          AND (
              data.id = latest_content.id
              OR (
                  next_data.id IS NOT NULL
                  AND (
                      next_data.title IS NULL
                      OR next_data.artist IS NULL
                      OR LOWER(next_data.title) IS DISTINCT FROM LOWER(data.title)
                      OR LOWER(next_data.artist) IS DISTINCT FROM LOWER(data.artist)
                  )
              )
              OR EXISTS (
                  SELECT 1
                  FROM song_verification_comment AS comment
                  WHERE comment.song_data_id = data.id
              )
          )
        ORDER BY country.name, data.entry_number, song.id,
                 data.created_at, data.id
        """,
        (year["id"],),
    )
    entries = cursor.fetchall()

    comments_by_data = defaultdict(list)
    cursor.execute(
        """
        SELECT comment.id, comment.song_data_id,
               comment.body, comment.created_at,
               account.username AS author_username
        FROM song_verification_comment AS comment
        JOIN song_data AS data ON data.id = comment.song_data_id
        JOIN account ON account.id = comment.author_id
        WHERE data.year_id = %s
        ORDER BY comment.created_at, comment.id
        """,
        (year["id"],),
    )
    for comment in cursor.fetchall():
        comments_by_data[comment["song_data_id"]].append(comment)

    songs = []
    historical_entries_by_song = defaultdict(list)
    deleted_entries = []
    comments_by_song = defaultdict(list)
    for entry in entries:
        entry["comments"] = comments_by_data[entry["id"]]
        is_current = (
            entry["song_id"] is not None
            and entry["id"] == entry["latest_data_id"]
            and not entry["song_deleted"]
        )
        if is_current:
            entry["id"] = entry["song_id"]
            songs.append(entry)
            comments_by_song[entry["song_id"]] = entry["comments"]
        elif not entry["song_deleted"]:
            historical_entries_by_song[entry["song_id"]].append(entry)
        else:
            deleted_entries.append(entry)

    verification_groups = []
    for song in songs:
        verification_groups.append(
            {
                "song": song,
                "historical_entries": historical_entries_by_song[song["id"]],
            }
        )

    deleted_entries_by_song = defaultdict(list)
    for entry in deleted_entries:
        deleted_entries_by_song[
            (entry["country_id"], entry["entry_number"])
        ].append(entry)
    for deleted_group in deleted_entries_by_song.values():
        verification_groups.append(
            {"song": None, "historical_entries": deleted_group}
        )

    def group_sort_key(group):
        representative = group["song"] or group["historical_entries"][0]
        return (
            (representative["country_name"] or representative["country_id"]).casefold(),
            representative["entry_number"] or 1,
            representative["country_id"],
        )

    verification_groups.sort(key=group_sort_key)

    verification_stats = {
        "pending": 0,
        "accepted": 0,
        "rejected": 0,
        "more-info": 0,
        "placeholders": 0,
    }
    for song in songs:
        if song["is_placeholder"]:
            verification_stats["placeholders"] += 1
        else:
            verification_stats[song["approval_status"]] += 1

    return render_template(
        "admin/verifications.html",
        year=year,
        comments_by_song=comments_by_song,
        verification_groups=verification_groups,
        verification_stats=verification_stats,
    )


@bp.get("/manage/<int:year>/verifications")
def verifications(year: int):
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT id, status, special_name, special_short_name
        FROM year
        WHERE id = %s AND id >= 0
        """,
        (year,),
    )
    year_data = cursor.fetchone()
    if not year_data:
        return render_template("error.html", error=f"Year {year} not found"), 404
    return _render_verifications(year_data)


@bp.get("/manage/special/<short_name>/verifications")
def verifications_special(short_name: str):
    year = _resolve_special(short_name)
    if not year:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404
    return _render_verifications(year)


def _add_comment(year_id: int, song_id: int, user: tuple[int, str], redirect_url: str):
    body = request.form.get("comment", "").strip()
    if not body:
        return render_template("error.html", error="Comment cannot be empty"), 400
    if len(body) > 2000:
        return render_template(
            "error.html", error="Comment cannot be longer than 2000 characters"
        ), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO song_verification_comment (song_data_id, author_id, body)
        SELECT song_data_id, %s, %s
        FROM current_song AS song
        WHERE id = %s AND year_id = %s
        RETURNING id
        """,
        (user[0], body, song_id, year_id),
    )
    if not cursor.fetchone():
        return render_template("error.html", error="Song not found in this year"), 404

    db.commit()
    return redirect(f"{redirect_url}#song-{song_id}")


def _set_verification(
    year_id: int,
    song_id: int,
    status: str,
    user: tuple[int, str],
    redirect_url: str,
):
    message = request.form.get("message", "").strip()
    if status in NOTIFICATION_STATUSES and not message:
        return render_template(
            "error.html",
            error="A message to the submitter is required for this status",
        ), 400
    if len(message) > MAX_NOTIFICATION_LENGTH:
        return render_template(
            "error.html",
            error=f"Message cannot be longer than {MAX_NOTIFICATION_LENGTH} characters",
        ), 400

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, song_data_id, submitter_id, title, artist
        FROM current_song AS song
        WHERE id = %s AND year_id = %s
        """,
        (song_id, year_id),
    )
    song = cursor.fetchone()
    if not song:
        return render_template("error.html", error="Song not found in this year"), 404
    status_change = set_song_status(
        cursor,
        song_id,
        approval_status=status,
        changed_by=user[0],
    )
    if status_change is not None and status in NOTIFICATION_STATUSES:
        _notify_submitter(cursor, song, status, user[0], message)

    db.commit()
    return redirect(f"{redirect_url}#song-{song_id}")


def _merge_replaced_song(
    year_id: int,
    version_id: int,
    user_id: int,
    redirect_url: str,
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT song.id AS song_id, current.song_data_id
        FROM song_data AS old
        JOIN song
          ON song.country_id = old.country_id
         AND song.year_id = old.year_id
         AND song.entry_number IS NOT DISTINCT FROM old.entry_number
        JOIN current_song AS current ON current.id = song.id
        WHERE old.id = %s AND song.year_id = %s AND current.year_id = %s
          AND old.id <> current.song_data_id
          AND current.title IS NOT NULL
          AND current.artist IS NOT NULL
        """,
        (version_id, year_id, year_id),
    )
    row = cursor.fetchone()
    if not row:
        return render_template("error.html", error="Song version not found"), 404

    cursor.execute(
        """
        UPDATE song_verification_comment
        SET song_data_id = %s
        WHERE song_data_id = %s
        """,
        (row["song_data_id"], version_id),
    )
    cursor.execute(
        """
        INSERT INTO song_revision_merge (
            song_data_id, merged_into_song_data_id, merged_by
        ) VALUES (%s, %s, %s)
        ON CONFLICT (song_data_id) DO UPDATE
        SET merged_into_song_data_id = EXCLUDED.merged_into_song_data_id,
            merged_by = EXCLUDED.merged_by,
            created_at = CURRENT_TIMESTAMP
        """,
        (version_id, row["song_data_id"], user_id),
    )
    db.commit()
    return redirect(f"{redirect_url}#song-{row['song_id']}")


def _hide_verification_revision(
    year_id: int,
    version_id: int,
    user_id: int,
    redirect_url: str,
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT old.song_id
        FROM song_data AS old
        WHERE old.id = %s
          AND old.year_id = %s
          AND old.title IS NOT NULL
          AND old.artist IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM current_song AS current
              WHERE current.song_data_id = old.id
          )
        """,
        (version_id, year_id),
    )
    row = cursor.fetchone()
    if not row:
        return render_template("error.html", error="Song version not found"), 404

    cursor.execute(
        """
        INSERT INTO song_verification_hidden_revision (song_data_id, hidden_by)
        VALUES (%s, %s)
        ON CONFLICT (song_data_id) DO UPDATE
        SET hidden_by = EXCLUDED.hidden_by,
            created_at = CURRENT_TIMESTAMP
        """,
        (version_id, user_id),
    )
    db.commit()
    anchor = f"#song-{row['song_id']}" if row["song_id"] is not None else ""
    return redirect(f"{redirect_url}{anchor}")


@bp.post("/manage/<int:year>/verifications/<int:song_id>/comments")
@require_user()
def add_verification_comment(year: int, song_id: int, user: tuple[int, str]):
    cursor = get_db().cursor()
    cursor.execute("SELECT id FROM year WHERE id = %s AND id >= 0", (year,))
    if not cursor.fetchone():
        return render_template("error.html", error=f"Year {year} not found"), 404
    return _add_comment(
        year,
        song_id,
        user,
        url_for("admin.verifications", year=year),
    )


@bp.post("/manage/<int:year>/verifications/<int:song_id>/status")
@require_user()
def set_verification_status(year: int, song_id: int, user: tuple[int, str]):
    cursor = get_db().cursor()
    cursor.execute("SELECT id FROM year WHERE id = %s AND id >= 0", (year,))
    if not cursor.fetchone():
        return render_template("error.html", error=f"Year {year} not found"), 404
    status = request.form.get("status")
    if status not in {"pending", "accepted", "rejected", "more-info"}:
        return render_template("error.html", error="Invalid verification status"), 400
    return _set_verification(
        year,
        song_id,
        status,
        user,
        url_for("admin.verifications", year=year),
    )


@bp.post("/manage/<int:year>/verifications/<int:version_id>/merge")
@require_user()
def merge_replaced_song(year: int, version_id: int, user: tuple[int, str]):
    cursor = get_db().cursor()
    cursor.execute("SELECT id FROM year WHERE id = %s AND id >= 0", (year,))
    if not cursor.fetchone():
        return render_template("error.html", error=f"Year {year} not found"), 404
    return _merge_replaced_song(
        year,
        version_id,
        user[0],
        url_for("admin.verifications", year=year),
    )


@bp.post("/manage/<int:year>/verifications/<int:version_id>/hide")
@require_user()
def hide_verification_revision(year: int, version_id: int, user: tuple[int, str]):
    cursor = get_db().cursor()
    cursor.execute("SELECT id FROM year WHERE id = %s AND id >= 0", (year,))
    if not cursor.fetchone():
        return render_template("error.html", error=f"Year {year} not found"), 404
    return _hide_verification_revision(
        year,
        version_id,
        user[0],
        url_for("admin.verifications", year=year),
    )


@bp.post("/manage/special/<short_name>/verifications/<int:song_id>/comments")
@require_user()
def add_verification_comment_special(
    short_name: str, song_id: int, user: tuple[int, str]
):
    year = _resolve_special(short_name)
    if not year:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404
    return _add_comment(
        year["id"],
        song_id,
        user,
        url_for("admin.verifications_special", short_name=short_name),
    )


@bp.post("/manage/special/<short_name>/verifications/<int:song_id>/status")
@require_user()
def set_verification_status_special(
    short_name: str, song_id: int, user: tuple[int, str]
):
    year = _resolve_special(short_name)
    if not year:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404
    status = request.form.get("status")
    if status not in {"pending", "accepted", "rejected", "more-info"}:
        return render_template("error.html", error="Invalid verification status"), 400
    return _set_verification(
        year["id"],
        song_id,
        status,
        user,
        url_for("admin.verifications_special", short_name=short_name),
    )


@bp.post("/manage/special/<short_name>/verifications/<int:version_id>/merge")
@require_user()
def merge_replaced_song_special(
    short_name: str, version_id: int, user: tuple[int, str]
):
    year = _resolve_special(short_name)
    if not year:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404
    return _merge_replaced_song(
        year["id"],
        version_id,
        user[0],
        url_for("admin.verifications_special", short_name=short_name),
    )


@bp.post("/manage/special/<short_name>/verifications/<int:version_id>/hide")
@require_user()
def hide_verification_revision_special(
    short_name: str, version_id: int, user: tuple[int, str]
):
    year = _resolve_special(short_name)
    if not year:
        return render_template("error.html", error=f"Special '{short_name}' not found"), 404
    return _hide_verification_revision(
        year["id"],
        version_id,
        user[0],
        url_for("admin.verifications_special", short_name=short_name),
    )
