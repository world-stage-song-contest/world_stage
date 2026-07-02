import io
import math

from flask import request

from ...db import fetchone, get_db
from ...utils import (
    ShowData,
    UserPermissions,
    get_show_id,
    render_template,
    with_permissions,
)
from .common import bp, get_other_shows, resolve_special


def generate_playlist(
    show_data: ShowData, postcards: bool, include_host: bool = True
) -> tuple[str, list[str]]:
    def write(buf: io.StringIO, val: str):
        buf.write(val)
        buf.write("\n")

    def write_header(buf: io.StringIO):
        write(buf, "#EXTINF:0")
        write(buf, "#EXTVLCOPT:network-caching=3000")

    def write_country(buf: io.StringIO, cc: str, url: str) -> str | None:
        if postcards:
            write_header(buf)
            write(buf, f"https://media.world-stage.org/postcards/{cc.lower()}.mov")

        write_header(buf)
        v = None
        if "media.world-stage.org" not in url:
            v = cc

        write(buf, url or "BAD LINK REPLACE ME THIS IS A BUG")

        return v

    def show_needs_host(show_data: ShowData) -> bool:
        # Specials have no host country, so never insert a host entry for them.
        if show_data.year is None or show_data.year < 0:
            return False

        if show_data.status != "draw":
            return False

        if not show_data.short_name.startswith("sf"):
            return False

        sn = int(show_data.short_name[2])
        return sn % 2 != 0

    db = get_db()
    cursor = db.cursor()

    insert_after = -1
    host = ""
    host_link = ""
    if include_host and show_needs_host(show_data):
        cursor.execute(
            """
            SELECT LOWER(country.id) AS cc, video_link FROM year
            JOIN country ON year.host_id = country.id
            JOIN song ON song.country_id = year.host_id
            WHERE year.id = %(y)s AND song.year_id = %(y)s
        """,
            {"y": show_data.year},
        )
        data = cursor.fetchone()
        if data:
            cursor.execute(
                """
                SELECT COUNT(id) AS c FROM song_show
                WHERE show_id = %s
            """,
                (show_data.id,),
            )
            insert_after = math.ceil(fetchone(cursor)["c"] / 2) - 1
            host = data.get("cc") or ""
            host_link = data.get("video_link") or ""

    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc, video_link FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY running_order
    """,
        (show_data.id,),
    )

    output = io.StringIO(newline="\r\n")
    output.write("#EXTM3U\n")

    bad_countries = []

    for i, song in enumerate(cursor.fetchall()):
        cc = song.get("cc") or ""
        url = song.get("video_link") or ""
        b = write_country(output, cc, url)
        if b is not None:
            bad_countries.append(b)

        if i == insert_after:
            write_country(output, host, host_link)

    write_header(output)
    write(
        output,
        f"https://media.world-stage.org/recaps/{abs(show_data.year):04d}{show_data.short_name}.mov",
    )

    return output.getvalue(), bad_countries


def get_show_play_entries(
    show_data: ShowData, postcards: bool
) -> tuple[list[dict], list[str]]:
    db = get_db()
    cursor = db.cursor()

    def show_needs_host(show_data: ShowData) -> bool:
        if show_data.year is None or show_data.year < 0:
            return False
        if show_data.status != "draw":
            return False
        if not show_data.short_name.startswith("sf"):
            return False
        return int(show_data.short_name[2]) % 2 != 0

    insert_after = -1
    host_row: dict | None = None
    if show_needs_host(show_data):
        cursor.execute(
            """
            SELECT LOWER(country.id) AS cc,
                   country.name AS country,
                   song.title,
                   song.artist,
                   song.video_link AS url,
                   song.poster_link,
                   song.vtt_link
            FROM year
            JOIN country ON year.host_id = country.id
            JOIN song ON song.country_id = year.host_id
            WHERE year.id = %(y)s AND song.year_id = %(y)s
            """,
            {"y": show_data.year},
        )
        host_row = cursor.fetchone()
        if host_row:
            cursor.execute(
                "SELECT COUNT(id) AS c FROM song_show WHERE show_id = %s",
                (show_data.id,),
            )
            insert_after = math.ceil(fetchone(cursor)["c"] / 2) - 1

    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc,
               country.name AS country,
               song.title,
               song.artist,
               song.video_link AS url,
               song.poster_link,
               song.vtt_link
        FROM song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY running_order
        """,
        (show_data.id,),
    )
    songs = cursor.fetchall()

    entries: list[dict] = []
    bad_countries: list[str] = []

    def append_song(row: dict):
        cc = (row.get("cc") or "").lower()
        url = row.get("url") or ""
        if "media.world-stage.org" not in url:
            bad_countries.append(cc)
        if postcards:
            entries.append(
                {
                    "kind": "postcard",
                    "cc": cc,
                    "country": row.get("country") or "",
                    "title": "",
                    "artist": "",
                    "url": f"https://media.world-stage.org/postcards/{cc}.mov",
                    "poster": None,
                    "vtt": None,
                }
            )
        entries.append(
            {
                "kind": "song",
                "cc": cc,
                "country": row.get("country") or "",
                "title": row.get("title") or "",
                "artist": row.get("artist") or "",
                "url": url,
                "poster": row.get("poster_link") or None,
                "vtt": row.get("vtt_link") or None,
            }
        )

    for i, song in enumerate(songs):
        append_song(song)
        if i == insert_after and host_row:
            append_song(host_row)

    entries.append(
        {
            "kind": "recap",
            "cc": "",
            "country": "",
            "title": "Recap",
            "artist": "",
            "url": (
                "https://media.world-stage.org/recaps/"
                f"{abs(show_data.year):04d}{show_data.short_name}.mov"
            ),
            "poster": None,
            "vtt": None,
        }
    )

    return entries, bad_countries


@bp.get("/<int:year>/<show>/play")
@with_permissions
def show_play(year: int, show: str, permissions: UserPermissions):
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    postcards = request.args.get("postcards", "false") == "true"

    entries, bad_countries = get_show_play_entries(show_data, postcards)

    if not permissions.can_view_restricted and bad_countries:
        bad_countries = sorted(set(bad_countries))
        return render_template(
            "error.html",
            error=(
                "Not all links for this show have been corrected. "
                "Please ping one of the admins. "
                f"Invalid links: {', '.join(bad_countries)}."
            ),
        )

    return render_template(
        "year/play.html",
        year=year,
        show=show,
        show_name=show_data.name,
        entries=entries,
        postcards=postcards,
        other_shows=get_other_shows(year, show),
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
        special=None,
        special_name=None,
    )


@bp.get("/special/<short_name>/<show>/play")
@with_permissions
def special_show_play(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    postcards = request.args.get("postcards", "false") == "true"

    entries, bad_countries = get_show_play_entries(show_data, postcards)

    if not permissions.can_view_restricted and bad_countries:
        bad_countries = sorted(set(bad_countries))
        return render_template(
            "error.html",
            error=(
                "Not all links for this show have been corrected. "
                "Please ping one of the admins. "
                f"Invalid links: {', '.join(bad_countries)}."
            ),
        )

    return render_template(
        "year/play.html",
        year=short_name,
        show=show,
        show_name=show_data.name,
        entries=entries,
        postcards=postcards,
        other_shows=get_other_shows(_year, show),
        can_apply_penalty=permissions.can_view_restricted,
        has_qualifiers=show_data.dtf is not None or show_data.sc is not None,
        special=short_name,
        special_name=special_year["special_name"],
    )
