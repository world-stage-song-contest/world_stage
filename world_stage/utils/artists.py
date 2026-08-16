import re
import unicodedata

MAX_ARTISTS_PER_ENTRY = 20
MAX_JOIN_LENGTH = 20


class ArtistValidationError(ValueError):
    pass


_NUMBERED_NAME_RE = re.compile(r"^(?P<name>.+?) \((?P<number>[1-9][0-9]*)\)$")


def artist_display_name(full_name: str, number: int) -> str:
    return full_name if number == 1 else f"{full_name} ({number})"


def parse_artist_display_name(display_name: str) -> tuple[str, int]:
    match = _NUMBERED_NAME_RE.fullmatch(display_name)
    if not match:
        return display_name, 1
    return match.group("name"), int(match.group("number"))


def _text(value, *, required: bool = False) -> str | None:
    if value is None:
        value = ""
    value = unicodedata.normalize("NFC", str(value)).replace("\r", "").strip()
    if required and not value:
        raise ArtistValidationError("must not be blank")
    return value or None


def _join_text(value) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFC", str(value)).replace("\r", "")
    return value if value.strip() else None


def parse_artist_credits(data: dict) -> list[dict]:
    """Validate the structured artist credits in an API request.

    Older clients may continue sending a single ``artist`` string. It is
    represented as one canonical artist whose stage name is the same string.
    """
    raw = data.get("artists")
    if raw is None:
        legacy = _text(data.get("artist"), required=True)
        return [{
            "id": None,
            "full_name": legacy,
            "native_name": None,
            "number": 1,
            "stage_name": legacy,
            "join": None,
        }]
    if not isinstance(raw, list) or not raw:
        raise ArtistValidationError("artists must be a non-empty list")
    if len(raw) > MAX_ARTISTS_PER_ENTRY:
        raise ArtistValidationError(
            f"an entry may have at most {MAX_ARTISTS_PER_ENTRY} artists"
        )

    credits = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ArtistValidationError(f"artists[{position}] must be an object")
        raw_id = item.get("id")
        try:
            artist_id = int(raw_id) if raw_id not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ArtistValidationError(
                f"artists[{position}].id must be an integer"
            ) from exc
        try:
            full_name = _text(item.get("full_name"), required=True)
            native_name = _text(item.get("native_name"))
            stage_name = _text(item.get("stage_name"))
            join = _join_text(item.get("join"))
            if position > 0 and join is None:
                raise ArtistValidationError("must provide a non-blank join")
        except ArtistValidationError as exc:
            raise ArtistValidationError(f"artists[{position}] {exc}") from exc
        raw_number = item.get("number")
        if artist_id is None and raw_number in (None, "") and full_name is not None:
            full_name, parsed_number = parse_artist_display_name(full_name)
            raw_number = parsed_number
        try:
            number = int(raw_number) if raw_number not in (None, "") else 1
        except (TypeError, ValueError) as exc:
            raise ArtistValidationError(
                f"artists[{position}].number must be a positive integer"
            ) from exc
        if number < 1:
            raise ArtistValidationError(
                f"artists[{position}].number must be a positive integer"
            )
        if position == 0:
            join = None
        elif join is not None and (len(join) > MAX_JOIN_LENGTH or "\n" in join):
            raise ArtistValidationError(
                f"artists[{position}].join must be at most {MAX_JOIN_LENGTH} characters"
            )
        credits.append({
            "id": artist_id,
            "full_name": full_name,
            "native_name": native_name,
            "number": number,
            "stage_name": stage_name,
            "join": join,
        })
    return credits


def render_artist_credits(credits: list[dict]) -> str:
    return "".join(
        (credit.get("join") or "") + (credit.get("stage_name") or credit["full_name"])
        for credit in credits
    )


def create_artist_credit_set(cursor, credits: list[dict]) -> int:
    cursor.execute("INSERT INTO artist_credit_set DEFAULT VALUES RETURNING id")
    credit_set_id = cursor.fetchone()["id"]
    for position, credit in enumerate(credits, 1):
        artist_id = credit["id"]
        if artist_id is not None:
            cursor.execute(
                "SELECT full_name, native_name, number FROM artist WHERE id = %s",
                (artist_id,),
            )
            artist = cursor.fetchone()
            if artist is None:
                raise ArtistValidationError(f"artist {artist_id} does not exist")
            if (
                credit["full_name"].casefold() == artist["full_name"].casefold()
                and credit["number"] == artist["number"]
            ):
                credit["full_name"] = artist["full_name"]
                credit["native_name"] = artist["native_name"]
                credit["number"] = artist["number"]
            else:
                # A stale autocomplete ID must never override text the user
                # edited after selecting that suggestion.
                artist_id = None
                credit["id"] = None
        if artist_id is None:
            cursor.execute(
                """
                SELECT id FROM artist
                WHERE LOWER(full_name) = LOWER(%s)
                  AND number = %s
                ORDER BY id
                LIMIT 1
                """,
                (credit["full_name"], credit["number"]),
            )
            artist = cursor.fetchone()
            if artist:
                artist_id = artist["id"]
            else:
                cursor.execute(
                    """INSERT INTO artist (full_name, native_name, number)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (credit["full_name"], credit["native_name"], credit["number"]),
                )
                artist_id = cursor.fetchone()["id"]
            credit["id"] = artist_id
        cursor.execute(
            """
            INSERT INTO artist_credit (
                artist_credit_set_id, position, artist_id, stage_name, join_phrase
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (credit_set_id, position, artist_id, credit["stage_name"], credit["join"]),
        )
    return credit_set_id


def fetch_artist_credits(cursor, credit_set_id: int | None) -> list[dict]:
    if credit_set_id is None:
        return []
    cursor.execute(
        """
        SELECT artist.id, artist.full_name, artist.native_name, artist.number,
               CASE WHEN artist.number = 1 THEN artist.full_name
                    ELSE artist.full_name || ' (' || artist.number || ')'
               END AS display_name,
               credit.stage_name, credit.join_phrase AS join
        FROM artist_credit AS credit
        JOIN artist ON artist.id = credit.artist_id
        WHERE credit.artist_credit_set_id = %s
        ORDER BY credit.position
        """,
        (credit_set_id,),
    )
    return cursor.fetchall()


def fetch_song_artist_credits(cursor, song_ids: list[int]) -> dict[int, list[dict]]:
    """Load ordered, linkable credits for several current songs at once."""
    if not song_ids:
        return {}
    cursor.execute(
        """
        SELECT song.id AS song_id, artist.full_name,
               CASE WHEN artist.number = 1 THEN artist.full_name
                    ELSE artist.full_name || ' (' || artist.number || ')'
               END AS display_name,
               credit.stage_name, credit.join_phrase AS join
        FROM current_song AS song
        JOIN artist_credit AS credit
          ON credit.artist_credit_set_id = song.artist_credit_set_id
        JOIN artist ON artist.id = credit.artist_id
        WHERE song.id = ANY(%s)
        ORDER BY song.id, credit.position
        """,
        (song_ids,),
    )
    credits = {song_id: [] for song_id in song_ids}
    for row in cursor.fetchall():
        credits[row.pop("song_id")].append(row)
    return credits
