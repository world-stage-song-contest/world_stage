"""Duration probing for files hosted on media.world-stage.org.

The radio's gapless schedule needs every song's length, so the
duration is probed with ffprobe (reads only the container metadata,
not the whole file) whenever a song's video link is set to a file on
the media host, and can be backfilled in bulk with the
``backfill-durations`` CLI command.
"""

import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import click
from flask import Flask
from flask.cli import with_appcontext

from .db import get_db

MEDIA_HOST = "media.world-stage.org"


def is_media_link(url: str | None) -> bool:
    if not url:
        return False
    return urllib.parse.urlparse(url).hostname == MEDIA_HOST


def probe_duration(url: str) -> float | None:
    """Duration in seconds of a remote media file, or None if it can't
    be determined (unreachable, not a media file, ffprobe missing)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def duration_for_link(
    url: str | None,
    old_url: str | None = None,
    old_duration: float | None = None,
) -> float | None:
    """The duration to store alongside a video_link write.

    Probes only links on the media host; anything else stores NULL.
    An unchanged link keeps its already-probed value so song edits
    that don't touch the link don't pay for a probe.
    """
    if not is_media_link(url):
        return None
    if url == old_url and old_duration is not None:
        return old_duration
    return probe_duration(url)


@click.command("backfill-durations")
@click.option("--workers", default=8, show_default=True, help="Concurrent probes.")
@click.option("--all", "reprobe_all", is_flag=True, help="Re-probe songs that already have a duration.")
@with_appcontext
def backfill_durations_command(workers: int, reprobe_all: bool):
    """Probe and store durations for songs hosted on media.world-stage.org."""
    db = get_db()
    cursor = db.cursor()
    where = "" if reprobe_all else "AND duration IS NULL"
    cursor.execute(
        f"""
        SELECT id, video_link
        FROM song
        WHERE video_link LIKE %s {where}
        ORDER BY id
        """,
        (f"https://{MEDIA_HOST}/%",),
    )
    songs = [row for row in cursor.fetchall() if is_media_link(row["video_link"])]
    if not songs:
        click.echo("Nothing to backfill.")
        return

    click.echo(f"Probing {len(songs)} songs...")
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        durations = pool.map(lambda s: probe_duration(s["video_link"]), songs)
        for i, (song, duration) in enumerate(zip(songs, durations), 1):
            if duration is None:
                failed += 1
                click.echo(f"  FAILED {song['video_link']}")
            else:
                cursor.execute(
                    "UPDATE song SET duration = %s WHERE id = %s",
                    (duration, song["id"]),
                )
            if i % 100 == 0:
                db.commit()
                click.echo(f"  {i}/{len(songs)}")
    db.commit()
    click.echo(f"Done: {len(songs) - failed} updated, {failed} failed.")


def init_app(app: Flask):
    app.cli.add_command(backfill_durations_command)
