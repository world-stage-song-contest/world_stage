from psycopg import sql

MAX_YEAR_SUBMISSIONS = 73


class NonPlaceholderLimitError(ValueError):
    """Raised when a status change would exceed a year's entry capacity."""


SONG_DATA_FIELDS = (
    "submitter_id",
    "title",
    "artist_credit_set_id",
    "native_title",
    "translated_lyrics",
    "romanized_lyrics",
    "native_lyrics",
    "video_link",
    "snippet_start",
    "snippet_end",
    "snippet2_start",
    "snippet2_end",
    "title_language_id",
    "native_language_id",
    "language_set_id",
    "genre_set_id",
    "key_signature_set_id",
    "time_signature_set_id",
    "notes",
    "sources",
    "poster_link",
    "vtt_link",
    "duration",
)


def latest_song_data(cursor, song_id: int) -> dict | None:
    cursor.execute(
        """
        SELECT data.*,
               artist_credit_name(data.artist_credit_set_id) AS rendered_artist
        FROM song
        JOIN song_data AS data
          ON data.song_id = song.id
          OR (
              data.song_id IS NULL
              AND data.country_id = song.country_id
              AND data.year_id = song.year_id
              AND data.entry_number IS NOT DISTINCT FROM song.entry_number
          )
        WHERE song.id = %s
        ORDER BY data.created_at DESC, data.id DESC
        LIMIT 1
        """,
        (song_id,),
    )
    return cursor.fetchone()


def create_song_revision(
    cursor,
    song_id: int,
    changes: dict,
    *,
    changed_by: int | None,
) -> dict:
    """Copy the latest metadata into a new immutable revision.

    Comments follow edits unless artist or title changed beyond capitalization.
    Song status is independent of content revisions.
    """
    previous = latest_song_data(cursor, song_id)
    if previous is None or previous["song_id"] is None:
        raise LookupError(f"Current song data for song {song_id} was not found")

    values = {field: previous[field] for field in SONG_DATA_FIELDS}
    values.update(changes)
    columns = sql.SQL(", ").join(
        map(sql.Identifier, ("song_id", *SONG_DATA_FIELDS, "changed_by"))
    )
    placeholders = sql.SQL(", ").join(
        sql.Placeholder() for _ in range(len(SONG_DATA_FIELDS) + 2)
    )
    cursor.execute(
        sql.SQL(
            "INSERT INTO song_data ({columns}) VALUES ({values}) RETURNING *"
        ).format(columns=columns, values=placeholders),
        (song_id, *(values[field] for field in SONG_DATA_FIELDS), changed_by),
    )
    revision = cursor.fetchone()

    def comparable(value):
        return value.casefold() if isinstance(value, str) else value

    cursor.execute(
        "SELECT artist_credit_name(%s) AS artist",
        (values["artist_credit_set_id"],),
    )
    rendered_artist = cursor.fetchone()["artist"]
    replaced = (
        comparable(previous["rendered_artist"]) != comparable(rendered_artist)
        or comparable(previous["title"]) != comparable(values["title"])
    )
    if not replaced:
        cursor.execute(
            """
            UPDATE song_verification_comment
            SET song_data_id = %s
            WHERE song_data_id = %s
            """,
            (revision["id"], previous["id"]),
        )
    return revision


def set_song_status(
    cursor,
    song_id: int,
    *,
    changed_by: int | None,
    approval_status: str | None = None,
    is_placeholder: bool | None = None,
) -> dict | None:
    """Append a song-level status snapshot when either status dimension changes."""
    cursor.execute(
        """
        SELECT approval_status, is_placeholder
        FROM song_status
        WHERE song_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (song_id,),
    )
    previous = cursor.fetchone()
    next_approval = approval_status or (
        previous["approval_status"] if previous else "pending"
    )
    next_placeholder = (
        is_placeholder
        if is_placeholder is not None
        else (previous["is_placeholder"] if previous else False)
    )
    if previous and (
        previous["approval_status"], previous["is_placeholder"]
    ) == (next_approval, next_placeholder):
        return None

    # Serialize placeholder removals within a year before checking capacity.
    # Excluding the target makes this work for both initial status creation
    # (current_song defaults to non-placeholder) and true -> false changes.
    if not next_placeholder and (previous is None or previous["is_placeholder"]):
        cursor.execute(
            """SELECT year_id FROM current_song WHERE id = %s""",
            (song_id,),
        )
        song = cursor.fetchone()
        if song is None:
            raise LookupError(f"Current song data for song {song_id} was not found")
        year_id = song["year_id"]
        if year_id >= 0:
            cursor.execute("SELECT id FROM year WHERE id = %s FOR UPDATE", (year_id,))
            cursor.execute(
                """SELECT COUNT(*) AS count
                   FROM current_song
                   WHERE year_id = %s AND id <> %s AND NOT is_placeholder""",
                (year_id, song_id),
            )
            if cursor.fetchone()["count"] >= MAX_YEAR_SUBMISSIONS:
                raise NonPlaceholderLimitError(
                    f"This year already has the maximum number of entries "
                    f"({MAX_YEAR_SUBMISSIONS})"
                )

    cursor.execute(
        """
        INSERT INTO song_status (
            song_id, song_data_id, approval_status, is_placeholder, changed_by
        )
        SELECT id, song_data_id, %s, %s, %s
        FROM current_song
        WHERE id = %s
        RETURNING *
        """,
        (next_approval, next_placeholder, changed_by, song_id),
    )
    status = cursor.fetchone()
    if status is None:
        raise LookupError(f"Current song data for song {song_id} was not found")
    return status


def withdraw_song(cursor, song_id: int, *, changed_by: int | None) -> int:
    """Append the all-null metadata sentinel used to withdraw a stable song."""
    cursor.execute(
        """
        INSERT INTO song_data (
            song_id, country_id, year_id, entry_number, modified_at, changed_by
        )
        SELECT NULL, country_id, year_id, entry_number, NULL, %s
        FROM song
        WHERE id = %s
        RETURNING id
        """,
        (changed_by, song_id),
    )
    sentinel = cursor.fetchone()
    if sentinel is None:
        raise LookupError(f"Song {song_id} was not found")
    return sentinel["id"]
