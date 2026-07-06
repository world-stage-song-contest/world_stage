import re
from collections import Counter, defaultdict
from decimal import Decimal

from flask import Blueprint, redirect, request, url_for

from ..db import get_db
from ..media import duration_for_link, is_media_link
from ..utils import (
    UserPermissions,
    get_closed_years,
    get_countries,
    get_country_name,
    get_country_songs,
    get_markdown_parser,
    get_show_results_for_songs,
    get_song,
    get_special_song,
    get_special_songs_for_country,
    render_template,
    require_permissions,
    resolve_country_code,
    with_auth,
)

bp = Blueprint("country", __name__, url_prefix="/country")


def _ordinal(n: int) -> str:
    suffix = (
        "th"
        if 10 <= n % 100 <= 20
        else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


def _format_decimal(value: Decimal | float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.1"))
    if rounded == rounded.to_integral():
        return str(rounded.to_integral())
    return str(rounded)


def _result_groups(entries: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for entry in entries:
        result = entry["result"]
        groups[(result["place"], result["total_countries"])].append(entry["label"])

    return [
        {
            "labels": labels,
            "place": place,
            "ordinal": _ordinal(place),
            "total": total,
        }
        for (place, total), labels in sorted(groups.items(), key=lambda item: item[0])
    ]


def _best_worst_results(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    result_entries = [entry for entry in entries if entry.get("result")]
    if not result_entries:
        return [], []

    best_pct = max(entry["result"]["placement_percentage"] for entry in result_entries)
    worst_pct = min(entry["result"]["placement_percentage"] for entry in result_entries)
    return (
        _result_groups(
            [
                entry
                for entry in result_entries
                if entry["result"]["placement_percentage"] == best_pct
            ]
        ),
        _result_groups(
            [
                entry
                for entry in result_entries
                if entry["result"]["placement_percentage"] == worst_pct
            ]
        ),
    )


def _qualification_stats(entries: list[dict]) -> dict | None:
    attempts = 0
    score = Decimal("0")
    for entry in entries:
        result = entry["results"]
        has_final = bool(result.get("f"))
        has_second_chance = bool(result.get("sc"))
        has_semi = bool(result.get("sf"))

        if not (has_semi or has_second_chance):
            continue

        attempts += 1
        if has_semi and has_second_chance:
            score += Decimal("0.5")
        if has_second_chance and has_final:
            score += Decimal("0.5")
        elif has_semi and has_final:
            score += Decimal("1")

    if attempts == 0:
        return None

    return {
        "percentage": (score / Decimal(attempts)) * 100,
        "score": score,
        "attempts": attempts,
    }


def _qualification_periods(entries: list[dict]) -> list[dict]:
    periods_by_id: dict[int, dict] = {}
    for entry in entries:
        period_id = entry["song"].year.id
        period = periods_by_id.setdefault(
            period_id,
            {
                "has_semi": False,
                "has_final_qualifier": False,
                "has_q_qualifier": False,
                "has_nq": False,
                "has_non_final": False,
            },
        )
        result = entry["results"]
        has_semi = bool(result.get("sf"))
        if not has_semi:
            continue

        period["has_semi"] = True
        if result.get("f"):
            period["has_final_qualifier"] = True
            period["has_q_qualifier"] = True
        elif result.get("sc"):
            period["has_q_qualifier"] = True

    for period in periods_by_id.values():
        period["has_nq"] = period["has_semi"] and not period["has_q_qualifier"]
        period["has_non_final"] = period["has_semi"] and not period["has_final_qualifier"]

    return list(periods_by_id.values())


def _current_streak(periods: list[dict], success_key: str) -> int:
    streak = 0
    for period in reversed(periods):
        if not period["has_semi"]:
            continue
        if period[success_key]:
            streak += 1
            continue
        break

    return streak


def _best_streak(periods: list[dict], success_key: str) -> int:
    best = 0
    streak = 0
    for period in periods:
        if not period["has_semi"]:
            continue
        if period[success_key]:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0

    return best


def _most_frequent_submitters(songs: list) -> list[dict]:
    counted_statuses = {"closed", "ongoing"}
    counts = Counter(
        song.submitter
        for song in songs
        if song.submitter and song.year.status in counted_statuses
    )
    if not counts:
        return []

    top_count = max(counts.values())
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items())
        if count == top_count
    ]


def _eligible_participation_entries(entries: list[dict]) -> list[dict]:
    counted_statuses = {"closed", "ongoing"}
    return [
        entry
        for entry in entries
        if entry["song"].year.status in counted_statuses
    ]


def _country_stats(
    songs: list,
    results: dict[int, dict],
    *,
    special: bool = False,
    ten_year_window: set[int] | None = None,
) -> dict:
    entries = [
        {
            "song": song,
            "label": song.year.special_name if special else str(song.year.id),
            "results": results.get(song.id, {}),
            "result": results.get(song.id, {}).get("year"),
        }
        for song in songs
    ]
    participation_entries = _eligible_participation_entries(entries)
    qualification_periods = _qualification_periods(participation_entries)
    best_results, worst_results = _best_worst_results(entries)

    final_entries = [
        {
            "label": entry["label"],
            "result": entry["results"]["f"],
        }
        for entry in entries
        if entry["results"].get("f")
    ]
    _, worst_final_results = _best_worst_results(final_entries)

    closed_results = [entry["result"] for entry in entries if entry.get("result")]
    wins = sum(1 for result in closed_results if result["place"] == 1)
    podiums = sum(1 for result in closed_results if result["place"] <= 3)
    top_fives = sum(1 for result in closed_results if result["place"] <= 5)
    top_tens = sum(1 for result in closed_results if result["place"] <= 10)
    avg_result = None
    if closed_results:
        avg_result = sum(
            Decimal(str(result["placement_percentage"])) for result in closed_results
        ) / len(closed_results)
    recent_results = [
        entry["result"]
        for entry in entries
        if entry.get("result")
        and ten_year_window is not None
        and entry["song"].year.id in ten_year_window
    ]
    recent_avg_result = None
    if recent_results:
        recent_avg_result = sum(
            Decimal(str(result["placement_percentage"])) for result in recent_results
        ) / len(recent_results)

    return {
        "participations": len(participation_entries),
        "first": participation_entries[0]["label"] if participation_entries else None,
        "latest": participation_entries[-1]["label"] if participation_entries else None,
        "best_results": best_results,
        "worst_results": worst_results,
        "finals": len(final_entries),
        "qualification": _qualification_stats(entries),
        "current_final_streak": _current_streak(
            qualification_periods,
            "has_final_qualifier",
        ),
        "best_final_streak": _best_streak(
            qualification_periods,
            "has_final_qualifier",
        ),
        "current_q_streak": _current_streak(
            qualification_periods,
            "has_q_qualifier",
        ),
        "best_q_streak": _best_streak(
            qualification_periods,
            "has_q_qualifier",
        ),
        "current_nq_streak": _current_streak(
            qualification_periods,
            "has_nq",
        ),
        "best_nq_streak": _best_streak(
            qualification_periods,
            "has_nq",
        ),
        "current_non_final_streak": _current_streak(
            qualification_periods,
            "has_non_final",
        ),
        "best_non_final_streak": _best_streak(
            qualification_periods,
            "has_non_final",
        ),
        "average_result": avg_result,
        "recent_average_result": recent_avg_result,
        "wins": wins,
        "podiums": podiums,
        "top_fives": top_fives,
        "top_tens": top_tens,
        "worst_final_results": worst_final_results,
        "most_frequent_submitters": _most_frequent_submitters(songs),
    }


@bp.get("/")
def index():
    countries = get_countries(only_participating=True)
    res = defaultdict(list)
    for c in countries:
        first_letter = c.name[0]
        res[first_letter].append(c)

    return render_template("country/index.html", countries=res)


@bp.get("/<code>/bias")
def bias(code: str):
    canonical = resolve_country_code(code.upper())
    if not canonical:
        return render_template("error.html", error=f"Country not found: {code}"), 404
    if canonical.lower() != code.lower():
        return redirect(url_for("country.bias", code=canonical.lower()), 301)

    name = get_country_name(canonical)
    year_from = request.args.get("from", type=int)
    year_to = request.args.get("to", type=int)

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM country_voter_bias(%s, %s, %s)", (canonical, year_from, year_to))
    biases = [dict(r) for r in cursor]

    return render_template(
        "inbound_bias.html",
        subject_type="country",
        subject_code=canonical,
        subject_name=name,
        biases=biases,
        closed_years=get_closed_years(),
        year_from=year_from,
        year_to=year_to,
        include_specials=True,
    )


@bp.get("/<code>")
def country(code: str):
    canonical = resolve_country_code(code.upper())
    if canonical and canonical.lower() != code.lower():
        return redirect(url_for("country.country", code=canonical.lower()), 301)
    songs = get_country_songs(code.upper(), select_languages=True)
    if not songs:
        return render_template("error.html", error=f"Songs not found for country {code}")
    name = get_country_name(code.upper())
    results = get_show_results_for_songs([s.id for s in songs])
    regular_songs = [s for s in songs if s.year.id >= 0]
    special_songs = [s for s in songs if s.year.id < 0]
    ten_year_window = set(get_closed_years()[-10:])
    return render_template(
        "country/country.html",
        songs=regular_songs,
        special_songs=special_songs,
        country=code,
        country_name=name,
        results=results,
        stats=_country_stats(regular_songs, results, ten_year_window=ten_year_window),
        special_stats=(
            _country_stats(special_songs, results, special=True) if special_songs else None
        ),
        format_decimal=_format_decimal,
    )


mime_types = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "m4a": "audio/mp4",
    "webm": "video/webm",
    "ogg": "video/ogg",
    # .mov files in this app are usually H.264/AAC inside a QuickTime
    # container, which browsers play happily when advertised as
    # video/mp4 — the canonical "video/quicktime" type makes Chrome
    # and Firefox refuse to attempt playback.
    "mov": "video/mp4",
}


def generate_iframe(url: str, img_url: str | None):
    if "youtu.be" in url:
        video_id = url.split("/")[-1]
        return (f'<iframe src="https://www.youtube.com/embed/{video_id}"'
                'frameborder="0" allowfullscreen></iframe>')

    elif "youtube.com/watch" in url:
        match = re.search(r"v=([^&]+)", url)
        if match:
            video_id = match.group(1)
            return (f'<iframe src="https://www.youtube.com/embed/{video_id}"'
                    'frameborder="0" allowfullscreen></iframe>')

    elif "drive.google.com/file/d/" in url:
        match = re.search(r"/d/([^/]+)", url)
        if match:
            file_id = match.group(1)
            return f'<iframe src="https://drive.google.com/file/d/{file_id}/preview"></iframe>'

    elif (suffix := url.rsplit(".", 1)[-1].lower()) in mime_types:
        mime_type = mime_types[suffix]
        poster = ""
        if mime_type.startswith("audio"):
            poster = f'poster="{img_url}"'
        return f'''<video id="video-player"
                    class="video-js vjs-fill"
                    controls
                    {poster}
                    preload="metadata"
                    data-setup='{{"responsive": true}}'>
        <source src="{url}" type="{mime_type}">
        This media format isn't supported for direct playback by your browser.
        <a href="{url}" target="_blank">Watch the video here</a>.
        </video>'''

    else:
        return f'''This media format isn't supported for direct playback by your browser.
        <a href="{url}" target="_blank">Watch the video here</a>.'''


@bp.get("/<code>/<int:year>")
@with_auth
def details(code: str, year: int, user: tuple[int, str] | None, permissions: UserPermissions):
    canonical = resolve_country_code(code.upper())
    if canonical and canonical.lower() != code.lower():
        return redirect(url_for("country.details", code=canonical.lower(), year=year), 301)
    song = get_song(year, code.upper())
    if not song:
        return render_template(
            "error.html", error=f"Songs not found for country {code} in year {year}"
        )
    url = song.video_link
    embed = ""
    if url and url != "N/A":
        embed = generate_iframe(url, song.poster_link)
    name = get_country_name(code.upper())

    user_id = user[0] if user else None
    can_edit = permissions.can_edit or user_id == song.submitter_id
    translated_lyrics = []
    latin_lyrics = []
    native_lyrics = []
    notes = []
    sources = song.sources or ""

    md = get_markdown_parser()

    if song.translated_lyrics:
        translated_lyrics = md.renderInline(song.translated_lyrics).split("\n")
    if song.latin_lyrics:
        latin_lyrics = md.renderInline(song.latin_lyrics).split("\n")
    if song.native_lyrics:
        native_lyrics = md.renderInline(song.native_lyrics).split("\n")
    if song.lyrics_notes:
        notes = md.renderInline(song.lyrics_notes).split("\n")

    rows = max(len(translated_lyrics), len(latin_lyrics), len(native_lyrics))
    columns = (
        (1 if translated_lyrics else 0) + (1 if latin_lyrics else 0) + (1 if native_lyrics else 0)
    )

    song_results = get_show_results_for_songs([song.id]).get(song.id, {})

    return render_template(
        "country/details.html",
        song=song,
        embed=embed,
        name=name,
        year=year,
        rows=rows,
        columns=columns,
        sources=sources,
        native_lyrics=native_lyrics,
        latin_lyrics=latin_lyrics,
        translated_lyrics=translated_lyrics,
        can_edit=can_edit,
        can_update_duration=permissions.can_edit,
        notes=notes,
        song_results=song_results,
    )


@bp.post("/duration/<int:song_id>")
@require_permissions(lambda p: p.can_edit)
def update_duration(song_id: int, permissions: UserPermissions):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT video_link FROM song WHERE id = %s", (song_id,))
    row = cursor.fetchone()
    if not row:
        return render_template("error.html", error=f"Song {song_id} not found"), 404

    duration = duration_for_link(row["video_link"])
    if is_media_link(row["video_link"]) and duration is None:
        return render_template(
            "error.html", error="Could not read the duration from the media file"
        ), 502

    cursor.execute("UPDATE song SET duration = %s WHERE id = %s", (duration, song_id))
    db.commit()
    return redirect(request.referrer or url_for("country.index"))


def _resolve_special(short_name: str) -> dict | None:
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, status, special_name, special_short_name FROM year
        WHERE special_short_name = %s""",
        (short_name,),
    )
    return cursor.fetchone()


def _render_song_details(
    song,
    name,
    special_short_name,
    special_name,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    """Render the song details page for a special song."""
    url = song.video_link
    embed = ""
    if url and url != "N/A":
        embed = generate_iframe(url, song.poster_link)

    user_id = user[0] if user else None
    can_edit = permissions.can_edit or user_id == song.submitter_id

    md = get_markdown_parser()
    translated_lyrics = (
        md.renderInline(song.translated_lyrics).split("\n") if song.translated_lyrics else []
    )
    latin_lyrics = md.renderInline(song.latin_lyrics).split("\n") if song.latin_lyrics else []
    native_lyrics = md.renderInline(song.native_lyrics).split("\n") if song.native_lyrics else []
    notes = md.renderInline(song.lyrics_notes).split("\n") if song.lyrics_notes else []
    sources = song.sources or ""

    rows = max(len(translated_lyrics), len(latin_lyrics), len(native_lyrics))
    columns = (
        (1 if translated_lyrics else 0) + (1 if latin_lyrics else 0) + (1 if native_lyrics else 0)
    )

    song_results = get_show_results_for_songs([song.id]).get(song.id, {})

    return render_template(
        "country/details.html",
        song=song,
        embed=embed,
        name=name,
        year=special_name,
        rows=rows,
        columns=columns,
        sources=sources,
        native_lyrics=native_lyrics,
        latin_lyrics=latin_lyrics,
        translated_lyrics=translated_lyrics,
        can_edit=can_edit,
        can_update_duration=permissions.can_edit,
        notes=notes,
        song_results=song_results,
        special=special_short_name,
        special_name=special_name,
    )


@bp.get("/<code>/<special_short_name>", defaults={"entry_number": None})
@bp.get("/<code>/<special_short_name>/<int:entry_number>")
@with_auth
def special_details(
    code: str,
    special_short_name: str,
    entry_number: int | None,
    user: tuple[int, str] | None,
    permissions: UserPermissions,
):
    # Don't match numeric year segments — those belong to the existing details() route
    try:
        int(special_short_name)
        return render_template("error.html", error="Not found"), 404
    except ValueError:
        pass

    canonical = resolve_country_code(code.upper())
    if canonical and canonical.lower() != code.lower():
        return redirect(
            url_for(
                "country.special_details",
                code=canonical.lower(),
                special_short_name=special_short_name,
                entry_number=entry_number,
            ),
            301,
        )

    special_year = _resolve_special(special_short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    year_id = special_year["id"]
    special_name = special_year["special_name"]
    name = get_country_name(code.upper())

    if entry_number is not None:
        # Direct song lookup by entry number
        song = get_special_song(year_id, code.upper(), entry_number)
        if not song:
            return render_template(
                "error.html",
                error=f"Song not found for {name} in {special_name} (entry {entry_number})",
            ), 404
        return _render_song_details(song, name, special_short_name, special_name, user, permissions)

    # No entry number — find all songs for this country in this special
    songs = get_special_songs_for_country(year_id, code.upper())
    if not songs:
        return render_template(
            "error.html", error=f"No songs found for {name} in {special_name}"
        ), 404

    if len(songs) == 1:
        # Only one entry — show it directly
        return _render_song_details(
            songs[0], name, special_short_name, special_name, user, permissions
        )

    # Multiple entries — show disambiguation page
    return render_template(
        "country/special_disambig.html",
        songs=songs,
        country=code,
        country_name=name,
        special=special_short_name,
        special_name=special_name,
    )
