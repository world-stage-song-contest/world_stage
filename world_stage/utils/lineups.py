def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def get_lineup_issues(cursor, show_id: int) -> list[dict[str, str]]:
    """Return blocking preflight issues for a show's voting lineup."""
    cursor.execute(
        """
        SELECT show.id, show.year_id, show.national_final_id,
               point_system.number AS declared_point_count,
               COUNT(point.id) AS point_count
        FROM show
        LEFT JOIN point_system ON point_system.id = show.point_system_id
        LEFT JOIN point ON point.point_system_id = point_system.id
        WHERE show.id = %s
        GROUP BY show.id, point_system.number
        """,
        (show_id,),
    )
    show = cursor.fetchone()
    if not show:
        return [_issue("show_missing", "The show does not exist.")]

    issues = []
    point_count = show["point_count"]
    if point_count == 0:
        issues.append(_issue("points_missing", "No points are configured for this show."))
    elif show["declared_point_count"] != point_count:
        issues.append(
            _issue(
                "point_count_mismatch",
                "The point system declares "
                f"{show['declared_point_count']} positions but contains {point_count} scores.",
            )
        )

    cursor.execute(
        """
        SELECT song_show.song_id, song_show.running_order,
               song.year_id AS song_year, song.main_participant,
               current_song.id IS NOT NULL AS has_metadata,
               BTRIM(current_song.title) <> ''
                   AND BTRIM(current_song.artist) <> '' AS metadata_not_blank,
               national_final_song.song_id IS NOT NULL AS belongs_to_national_final
        FROM song_show
        JOIN song ON song.id = song_show.song_id
        LEFT JOIN current_song ON current_song.id = song.id
        LEFT JOIN national_final_song
          ON national_final_song.national_final_id = %s
         AND national_final_song.song_id = song.id
        WHERE song_show.show_id = %s
        ORDER BY song_show.running_order NULLS LAST, song_show.id
        """,
        (show["national_final_id"], show_id),
    )
    entries = cursor.fetchall()
    entry_count = len(entries)
    if not entries:
        issues.append(_issue("lineup_empty", "The lineup is empty."))
        return issues

    if point_count and entry_count < point_count:
        issues.append(
            _issue(
                "insufficient_entries",
                f"The lineup has {entry_count} entries, but the point system ranks "
                f"{point_count}.",
            )
        )

    missing_order = sum(entry["running_order"] is None for entry in entries)
    invalid_order = sum(
        entry["running_order"] is not None and entry["running_order"] < 1
        for entry in entries
    )
    orders = [
        entry["running_order"]
        for entry in entries
        if entry["running_order"] is not None and entry["running_order"] > 0
    ]
    duplicates = sorted({order for order in orders if orders.count(order) > 1})
    if missing_order:
        issues.append(
            _issue(
                "running_order_missing",
                f"{missing_order} lineup "
                f"entr{'y has' if missing_order == 1 else 'ies have'} "
                "no running-order position.",
            )
        )
    if invalid_order:
        issues.append(
            _issue(
                "running_order_invalid",
                f"{invalid_order} running-order "
                f"position{' is' if invalid_order == 1 else 's are'} below 1.",
            )
        )
    if duplicates:
        issues.append(
            _issue(
                "running_order_duplicate",
                "Running-order positions must be unique; duplicated: "
                + ", ".join(str(order) for order in duplicates)
                + ".",
            )
        )
    if not missing_order and not invalid_order and not duplicates:
        expected = list(range(1, entry_count + 1))
        if sorted(orders) != expected:
            issues.append(
                _issue(
                    "running_order_noncontiguous",
                    f"Running order must use every position from 1 to {entry_count}.",
                )
            )

    wrong_year = sum(entry["song_year"] != show["year_id"] for entry in entries)
    if wrong_year:
        issues.append(
            _issue(
                "entry_wrong_year",
                f"{wrong_year} lineup "
                f"entr{'y belongs' if wrong_year == 1 else 'ies belong'} "
                "to a different year.",
            )
        )
    missing_metadata = sum(
        not entry["has_metadata"] or not entry["metadata_not_blank"] for entry in entries
    )
    if missing_metadata:
        issues.append(
            _issue(
                "metadata_missing",
                f"{missing_metadata} lineup "
                f"entr{'y is' if missing_metadata == 1 else 'ies are'} "
                "missing an artist or song title.",
            )
        )

    if show["national_final_id"] is not None:
        outside_contest = sum(not entry["belongs_to_national_final"] for entry in entries)
        if outside_contest:
            issues.append(
                _issue(
                    "national_final_candidate_missing",
                    f"{outside_contest} lineup "
                    f"entr{'y is' if outside_contest == 1 else 'ies are'} "
                    "not a candidate in this national final.",
                )
            )
    else:
        unpromoted = sum(not entry["main_participant"] for entry in entries)
        if unpromoted:
            issues.append(
                _issue(
                    "main_participant_missing",
                    f"{unpromoted} lineup "
                    f"entr{'y is' if unpromoted == 1 else 'ies are'} "
                    "not marked as a main-contest participant.",
                )
            )

    return issues


def get_unassigned_lineup_issue(
    cursor, year_id: int, national_final_id: int | None
) -> dict[str, str] | None:
    """Return a contest-level issue for candidates absent from every show."""
    if national_final_id is not None:
        cursor.execute(
            """
            SELECT COUNT(*) AS count
            FROM national_final_song
            WHERE national_final_id = %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM song_show
                  JOIN show ON show.id = song_show.show_id
                  WHERE song_show.song_id = national_final_song.song_id
                    AND show.national_final_id = %s
              )
            """,
            (national_final_id, national_final_id),
        )
        count = cursor.fetchone()["count"]
        if count:
            return _issue(
                "national_final_candidate_unassigned",
                f"{count} national-final candidate{' is' if count == 1 else 's are'} "
                "not assigned to any show.",
            )
        return None

    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM song
        WHERE song.year_id = %s
          AND song.main_participant
          AND NOT EXISTS (
              SELECT 1
              FROM song_show
              JOIN show ON show.id = song_show.show_id
              WHERE song_show.song_id = song.id
                AND show.year_id = %s
                AND show.national_final_id IS NULL
          )
        """,
        (year_id, year_id),
    )
    count = cursor.fetchone()["count"]
    if count:
        return _issue(
            "main_participant_unassigned",
            f"{count} main contest participant{' is' if count == 1 else 's are'} "
            "not assigned to any show.",
        )
    return None
