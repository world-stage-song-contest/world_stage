from urllib.parse import urlsplit


def is_media_url(value: object) -> bool:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme in ("http", "https")
            and url.netloc.lower() == "media.world-stage.org"
        )
    except ValueError:
        return False


def validate_metadata(metadata: object, *, year: bool = False) -> str | None:
    if not isinstance(metadata, dict):
        return "Metadata must be a JSON object."
    if year:
        for key in ("opening", "countdown"):
            if key in metadata and not isinstance(metadata[key], bool):
                return f"{key} must be a boolean."
    else:
        if metadata.get("opening") is not None and not is_media_url(metadata["opening"]):
            return "Opening must be a media.world-stage.org URL or null."
        if "intervals" in metadata:
            intervals = metadata["intervals"]
            if not isinstance(intervals, list) or not all(map(is_media_url, intervals)):
                return "Intervals must be an array of media.world-stage.org URLs."
    return None


def interval_chunks(intervals: list[str]) -> list[list[str]]:
    size, extra = divmod(len(intervals), 3)
    chunks = []
    start = 0
    for index in range(3):
        end = start + size + (index < extra)
        chunks.append(intervals[start:end])
        start = end
    return chunks


def _get_opening_act_country(cursor, year: int, short_name: str) -> str | None:
    cursor.execute(
        """SELECT short_name FROM show
           WHERE year_id = %s AND national_final_id IS NULL
           ORDER BY show_number DESC NULLS LAST""",
        (year,),
    )
    names = [row["short_name"] for row in cursor.fetchall()]
    semifinals = [name for name in names if name.startswith("sf")]
    if short_name == "f":
        placement = 1
    elif short_name == "sc":
        placement = 2
    elif short_name in semifinals:
        placement = semifinals.index(short_name) + 2 + ("sc" in names)
    else:
        return None

    cursor.execute(
        """
        SELECT LOWER(country_id) AS cc
        FROM country_year_results
        WHERE year_id = %s AND place = %s
        """,
        (year - 1, placement),
    )
    row = cursor.fetchone()
    return row["cc"] if row else None


def get_show_segments(cursor, show_id: int) -> tuple[list[dict], list[dict]]:
    cursor.execute(
        """SELECT show.year_id, show.metadata, year.metadata AS year_metadata,
                  COALESCE(nf.short_name || '-', '') || show.short_name AS short_name
           FROM show
           JOIN year ON year.id = show.year_id
           LEFT JOIN national_final AS nf ON nf.id = show.national_final_id
           WHERE show.id = %s""",
        (show_id,),
    )
    show = cursor.fetchone()
    if not show:
        return [], []
    year = show["year_id"]
    short_name = show["short_name"]
    metadata = show["metadata"]
    year_metadata = show["year_metadata"]
    media = "https://media.world-stage.org"

    def entry(kind: str, title: str, url: str) -> dict:
        return {
            "kind": kind, "title": title, "url": url, "shuffleable": False,
            "cc": "", "country": "", "artist": "", "poster": None, "vtt": None,
        }

    opening_url = (
        f"{media}/openings/{year}.mov" if year_metadata.get("opening") is True
        else f"{media}/openings/ws_opening.json"
    )
    intro = [entry("opening", f"WS {year} Opening", opening_url)]
    opening_act = metadata.get("opening")
    if not opening_act and year >= 0:
        country = _get_opening_act_country(cursor, year, short_name)
        if country:
            opening_act = f"{media}/ws{year - 1}{country}.json"
    if opening_act:
        intro.append(entry("opening", "Opening act", opening_act))

    countdown = (
        f"{media}/countdown/{year}.mov" if year_metadata.get("countdown") is True
        else f"{media}/countdown/countdown_with_sound.json"
    )
    outro = [entry("announcement", "Voting announcement", f"{media}/silence/silence.mov")]
    breaks = [
        entry("recap", "Recap 1", f"{media}/recaps/{abs(year):04d}{short_name}.mov"),
        entry("recap", "Recap 2", f"{media}/recaps/{abs(year):04d}{short_name}s.mov"),
        entry("countdown", "Countdown", countdown),
    ]
    interval_number = 0
    for segment, chunk in zip(breaks, interval_chunks(metadata.get("intervals", [])), strict=True):
        outro.append(segment)
        for url in chunk:
            interval_number += 1
            outro.append(entry("interval", f"Interval act {interval_number}", url))
    return intro, outro
