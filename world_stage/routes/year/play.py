from flask import request

from ... import listens, scrobble
from ...db import get_db
from ...utils import (
    ShowData,
    UserPermissions,
    can_manage_show,
    get_show_id,
    get_user_id_from_session,
    render_template,
    with_auth,
)
from ...utils.booleans import query_bool
from ...utils.playlist_media import resolve_playlist_media
from ...utils.playlists import format_m3u, song_play_entries
from ...utils.show_metadata import get_show_segments
from .common import bp, resolve_special


def generate_playlist(
    show_data: ShowData, postcards: bool, include_host: bool = True, intervals: bool = False
) -> tuple[str, list[str]]:
    entries, bad_countries = get_show_play_entries(
        show_data, postcards, intervals, include_host
    )
    urls = (entry["url"] for entry in entries)
    if intervals:
        urls = (resolve_playlist_media(url) for url in urls)
    return format_m3u(urls), bad_countries


def get_show_play_entries(
    show_data: ShowData, postcards: bool, full_show: bool = False, include_host: bool = True
) -> tuple[list[dict], list[str]]:
    db = get_db()
    cursor = db.cursor()

    def show_needs_host(show_data: ShowData) -> bool:
        if show_data.year is None or show_data.year < 0:
            return False
        if show_data.status not in ("draw", "partial", "full"):
            return False
        if show_data.national_final_id is not None or not show_data.short_name.startswith("sf"):
            return False
        return int(show_data.short_name[2:]) % 2 != 0

    host_row: dict | None = None
    if include_host and show_needs_host(show_data):
        cursor.execute(
            """
            SELECT LOWER(country.id) AS cc,
                   country.name AS country,
                   song.id,
                   song.title,
                   song.artist,
                   song.duration,
                   song.video_link,
                   song.poster_link,
                   song.vtt_link
            FROM year
            JOIN country ON year.host_id = country.id
            JOIN current_song AS song ON song.country_id = year.host_id
            WHERE year.id = %(y)s AND song.year_id = %(y)s
              AND NOT EXISTS (
                  SELECT 1 FROM song_show WHERE song_show.show_id = %(show_id)s
                    AND song_show.song_id = song.id
              )
            ORDER BY song.entry_number
            LIMIT 1
            """,
            {"y": show_data.year, "show_id": show_data.id},
        )
        host_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT LOWER(country.id) AS cc,
               country.name AS country,
               song.id,
               song.title,
               song.artist,
               song.duration,
               song.video_link,
               song.poster_link,
               song.vtt_link
        FROM current_song AS song
        JOIN song_show ON song_show.song_id = song.id
        JOIN country ON song.country_id = country.id
        WHERE song_show.show_id = %s
        ORDER BY running_order
        """,
        (show_data.id,),
    )
    songs = cursor.fetchall()

    if host_row and songs:
        songs.insert((len(songs) + 1) // 2, host_row)
    entries, bad_countries = song_play_entries(songs, postcards)

    if full_show:
        intro, outro = get_show_segments(cursor, show_data.id)
        return intro + entries + outro, bad_countries

    entries.append(
        {
            "kind": "recap",
            "shuffle_group": "recap",
            "shuffleable": False,
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


def _scrobble_enabled() -> bool:
    user = get_user_id_from_session(request.cookies.get("session"))
    return bool(user) and scrobble.has_enabled_account(user[0])


def _render_show_player(show_data: ShowData, user, permissions: UserPermissions, special_year=None):
    postcards = query_bool(request.args, "postcards", True)

    full_show = query_bool(request.args, "intervals", query_bool(request.args, "full_show", False))
    include_host = query_bool(request.args, "host", show_data.status != "full")
    entries, bad_countries = get_show_play_entries(show_data, postcards, full_show, include_host)

    elevated = can_manage_show(show_data, user, permissions)
    if not elevated and bad_countries:
        bad_countries = sorted(set(bad_countries))
        return render_template(
            "error.html",
            error=(
                "Not all links for this show have been corrected. "
                "Please ping one of the admins. "
                f"Invalid links: {', '.join(bad_countries)}."
            ),
        )

    listens.attach_snapshots(entries)
    return render_template(
        "year/play.html",
        year=special_year["special_short_name"] if special_year else show_data.year,
        show=show_data.short_name,
        show_name=show_data.name,
        entries=entries,
        postcards=postcards,
        full_show=full_show,
        include_host=include_host,
        host_available=show_data.status == "full",
        special=special_year["special_short_name"] if special_year else None,
        special_name=special_year["special_name"] if special_year else None,
        scrobble_enabled=_scrobble_enabled(),
        play_count_enabled=bool(user),
    )


@bp.get("/<int:year>/<show>/play")
@with_auth
def show_play(year: int, show: str, user, permissions: UserPermissions):
    show_data = get_show_id(show, year)
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    return _render_show_player(show_data, user, permissions)


@bp.get("/special/<short_name>/<show>/play")
@with_auth
def special_show_play(short_name: str, show: str, user, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404
    show_data = get_show_id(show, special_year["id"])
    if not show_data:
        return render_template("error.html", error="Show not found"), 404
    return _render_show_player(show_data, user, permissions, special_year)
