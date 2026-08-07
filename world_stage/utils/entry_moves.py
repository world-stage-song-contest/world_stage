from dataclasses import dataclass

from .song_revisions import create_song_revision, withdraw_song


@dataclass
class EntryMoveError(ValueError):
    message: str

    def __str__(self) -> str:
        return self.message


def move_entry(
    cursor,
    song_id: int,
    *,
    to_year: int,
    to_country: str,
    changed_by: int,
    submitter_id: int | None = None,
) -> dict:
    """Move a regular entry between years that are open for submissions.

    ``submitter_id`` limits the operation to that user's entries. Admin callers
    leave it unset. A placeholder at the destination is withdrawn and parked on
    an unused entry number so its immutable history remains addressable.
    """
    params: list[object] = [song_id]
    owner_filter = ""
    if submitter_id is not None:
        owner_filter = "AND current.submitter_id = %s"
        params.append(submitter_id)

    cursor.execute(
        f"""
        SELECT current.id, current.year_id, current.country_id,
               current.entry_number, source_year.submissions_open
        FROM song AS stable
        JOIN current_song AS current ON current.id = stable.id
        JOIN year AS source_year ON source_year.id = current.year_id
        WHERE current.id = %s AND current.main_participant {owner_filter}
        FOR UPDATE OF stable
        """,
        params,
    )
    source = cursor.fetchone()
    if source is None:
        raise EntryMoveError("Entry not found")
    if not source["submissions_open"]:
        raise EntryMoveError("Entries can only be moved from a year open for submissions")

    cursor.execute(
        "SELECT id, submissions_open FROM year WHERE id = %s FOR UPDATE",
        (to_year,),
    )
    destination_year = cursor.fetchone()
    if destination_year is None or not destination_year["submissions_open"]:
        raise EntryMoveError("Entries can only be moved to a year open for submissions")

    cursor.execute(
        "SELECT id FROM country WHERE id = %s AND is_participating",
        (to_country,),
    )
    if cursor.fetchone() is None:
        raise EntryMoveError("Invalid destination country")

    cursor.execute(
        """
        SELECT 1 FROM national_final
        WHERE year_id = %s AND owner_country_id = %s
          AND status <> 'cancelled'
        """,
        (to_year, to_country),
    )
    if cursor.fetchone() is not None:
        raise EntryMoveError(
            "This country is being selected through a national final"
        )

    if source["year_id"] == to_year and source["country_id"] == to_country:
        raise EntryMoveError("The entry is already in that slot")

    cursor.execute(
        """
        SELECT current.id, current.is_placeholder, current.entry_number
        FROM song AS stable
        JOIN current_song AS current ON current.id = stable.id
        WHERE current.year_id = %s AND current.country_id = %s
        ORDER BY current.entry_number
        FOR UPDATE OF stable
        """,
        (to_year, to_country),
    )
    occupants = cursor.fetchall()
    if any(not row["is_placeholder"] for row in occupants):
        raise EntryMoveError("The destination already contains a non-placeholder entry")
    if len(occupants) > 1:
        raise EntryMoveError("The destination contains multiple placeholder slots")

    destination_entry_number = (
        occupants[0]["entry_number"] if occupants else source["entry_number"]
    )
    if occupants:
        placeholder_id = occupants[0]["id"]
        sentinel_id = withdraw_song(cursor, placeholder_id, changed_by=changed_by)
        # Keep the withdrawal attached to the placeholder even after parking its
        # stable row elsewhere; otherwise the old content would become current.
        cursor.execute(
            "UPDATE song_data SET song_id = %s WHERE id = %s",
            (placeholder_id, sentinel_id),
        )
        cursor.execute(
            """
            SELECT COALESCE(MAX(entry_number), 0) + 1 AS next_entry_number
            FROM song
            WHERE year_id = %s AND country_id = %s
            """,
            (to_year, to_country),
        )
        parked_entry_number = cursor.fetchone()["next_entry_number"]
        cursor.execute(
            "UPDATE song SET entry_number = %s WHERE id = %s",
            (parked_entry_number, placeholder_id),
        )

    cursor.execute(
        """
        UPDATE song
        SET year_id = %s, country_id = %s, entry_number = %s
        WHERE id = %s
        """,
        (to_year, to_country, destination_entry_number, song_id),
    )
    revision = create_song_revision(cursor, song_id, {}, changed_by=changed_by)
    # Comments may live on several visible revisions. Moving is not a song
    # replacement, so all of them belong with the entry at its new location.
    cursor.execute(
        """
        UPDATE song_verification_comment
        SET song_data_id = %s
        WHERE song_data_id IN (
            SELECT id FROM song_data WHERE song_id = %s AND id <> %s
        )
        """,
        (revision["id"], song_id, revision["id"]),
    )
    return {
        "id": song_id,
        "year_id": to_year,
        "country_id": to_country,
        "entry_number": destination_entry_number,
    }
