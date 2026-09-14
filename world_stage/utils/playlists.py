import io
from collections.abc import Iterable


def format_m3u(urls: Iterable[str | None]) -> str:
    output = io.StringIO(newline="\r\n")
    output.write("#EXTM3U\n")
    for url in urls:
        output.write("#EXTINF:0\n#EXTVLCOPT:network-caching=3000\n")
        output.write((url or "BAD LINK REPLACE ME THIS IS A BUG") + "\n")
    return output.getvalue()


def song_play_entries(rows: list[dict], postcards: bool) -> tuple[list[dict], list[str]]:
    """Build player entries from catalog songs."""
    entries: list[dict] = []
    bad_countries: list[str] = []
    for index, row in enumerate(rows):
        cc = (row.get("cc") or "").lower()
        url = row.get("video_link") or ""
        shuffle_group = f"entry-{index}"
        if "media.world-stage.org" not in url:
            bad_countries.append(cc)
        if postcards:
            entries.append(
                {
                    "kind": "postcard",
                    "shuffle_group": shuffle_group,
                    "shuffleable": True,
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
                "shuffle_group": shuffle_group,
                "shuffleable": True,
                "id": row["id"],
                "cc": cc,
                "country": row.get("country") or "",
                "title": row.get("title") or "",
                "artist": row.get("artist") or "",
                "duration": row.get("duration"),
                "url": url,
                "poster": row.get("poster_link") or None,
                "vtt": row.get("vtt_link") or None,
            }
        )
    return entries, bad_countries


