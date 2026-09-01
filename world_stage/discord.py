import json
import urllib.error
import urllib.request

from flask import current_app

from .db import get_db

SUBDIVISION_FLAGS = {
    "EN": "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "AB": "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "WA": "🏴\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
}
CUSTOM_FLAGS = {
    "YU": ("flag_yu", "DISCORD_FLAG_YU_EMOJI_ID"),
    "DD": ("flag_dd", "DISCORD_FLAG_DD_EMOJI_ID"),
}


class DiscordNotificationError(RuntimeError):
    pass


def _flag_emoji(country_id: str) -> str:
    code = country_id.upper()
    if code in SUBDIVISION_FLAGS:
        return SUBDIVISION_FLAGS[code]
    if code in CUSTOM_FLAGS:
        name, config_key = CUSTOM_FLAGS[code]
        emoji_id = current_app.config.get(config_key, "").strip()
        return f"<:{name}:{emoji_id}>" if emoji_id else f":{name}:"
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code)


def _clip_title(title: str, limit: int = 25) -> str:
    if len(title) <= limit:
        return title

    title_words = title.split()
    if len(title_words) == 1:
        return title_words[0]

    words = [word for word in title_words if len(word) <= limit]
    if not words:
        return ""
    cleaned = " ".join(words)
    if len(cleaned) <= limit:
        return cleaned

    clipped = words[0]
    for word in words[1:]:
        candidate = f"{clipped} {word}"
        if len(candidate) + 1 > limit:
            break
        clipped = candidate
    return clipped if len(clipped) == limit else f"{clipped}…"


def _entry_label(row: dict) -> str:
    return f"{_flag_emoji(row['country_id'])} {_clip_title(row['title'])}".rstrip()


def _destination_label(show_type: str, show_name: str) -> str:
    if show_type == "f":
        return "Final"
    if show_type == "sc":
        return "Repe"
    return f"To {show_name}"


def _notification_payload(show_id: int) -> dict:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT source.show_name, source.show_type AS source_show_type,
               source.year_id,
               year.special_name,
               target.id AS target_show_id, target.show_name AS target_show_name,
               target.show_type AS target_show_type,
               qualifier.qualifier_order, song.country_id, song.title
        FROM show AS source
        JOIN year ON year.id = source.year_id
        JOIN show_qualifier AS qualifier ON qualifier.source_show_id = source.id
        JOIN show AS target ON target.id = qualifier.target_show_id
        JOIN current_song AS song ON song.id = qualifier.song_id
        JOIN show_progression AS progression
          ON progression.source_show_id = qualifier.source_show_id
         AND progression.target_show_id = qualifier.target_show_id
        WHERE source.id = %s AND source.national_final_id IS NULL
        ORDER BY progression.priority, qualifier.qualifier_order
        """,
        (show_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        raise DiscordNotificationError("No qualifiers have been saved for this show")

    first = rows[0]
    event_name = first["special_name"] or str(first["year_id"])
    grouped: dict[int, dict] = {}
    for row in rows:
        group = grouped.setdefault(
            row["target_show_id"],
            {
                "name": _destination_label(
                    row["target_show_type"], row["target_show_name"]
                ),
                "entries": [],
            },
        )
        group["entries"].append(_entry_label(row))

    reveal_number = 1
    width = max(2, len(str(len(rows))))
    for group in grouped.values():
        numbered_entries = []
        for entry in group["entries"]:
            numbered_entries.append(f"`{reveal_number:0{width}d}` {entry}")
            reveal_number += 1
        group["entries"] = numbered_entries

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{event_name}: {first['show_name']} qualifiers",
                "fields": [
                    {
                        "name": group["name"],
                        "value": "\n".join(group["entries"]),
                        "inline": first["source_show_type"] != "sc",
                    }
                    for group in grouped.values()
                ],
            }
        ],
    }


def _running_order_payload(show_id: int) -> dict:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show.show_name, show.year_id, year.special_name,
               song_show.running_order, song.country_id, song.title
        FROM show
        JOIN year ON year.id = show.year_id
        JOIN song_show ON song_show.show_id = show.id
        JOIN current_song AS song ON song.id = song_show.song_id
        WHERE show.id = %s AND show.national_final_id IS NULL
        ORDER BY song_show.running_order
        """,
        (show_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        raise DiscordNotificationError("This show has no running order")

    first = rows[0]
    event_name = first["special_name"] or str(first["year_id"])
    total = len(rows)
    midpoint = (total + 1) // 2
    width = max(2, len(str(total)))
    sections = [(1, midpoint, rows[:midpoint])]
    if midpoint < total:
        sections.append((midpoint + 1, total, rows[midpoint:]))

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{event_name}: {first['show_name']} running order",
                "fields": [
                    {
                        "name": f"{start}-{end}",
                        "value": "\n".join(
                            f"`{row['running_order']:0{width}d}` {_entry_label(row)}"
                            for row in section
                        ),
                        "inline": True,
                    }
                    for start, end, section in sections
                ],
            }
        ],
    }


def _final_results_payload(show_id: int) -> dict:
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT show.show_name, show.year_id, year.special_name,
               result.place, result.total_points, song.country_id, song.title
        FROM show
        JOIN year ON year.id = show.year_id
        JOIN country_show_results AS result
          ON result.show_id = show.id AND result.result_mode = 'official'
        JOIN current_song AS song ON song.id = result.song_id
        WHERE show.id = %s
          AND show.show_type = 'f'
          AND show.national_final_id IS NULL
        ORDER BY result.place
        """,
        (show_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        raise DiscordNotificationError("This final has no results")

    first = rows[0]
    event_name = first["special_name"] or str(first["year_id"])
    total = len(rows)
    midpoint = (total + 1) // 2
    width = max(2, len(str(total)))
    sections = [(1, midpoint, rows[:midpoint])]
    if midpoint < total:
        sections.append((midpoint + 1, total, rows[midpoint:]))

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": f"{event_name}: {first['show_name']} results",
                "fields": [
                    {
                        "name": f"{start}-{end}",
                        "value": "\n".join(
                            f"`{row['place']:0{width}d}` {_entry_label(row)} "
                            f"**{row['total_points']}**"
                            for row in section
                        ),
                        "inline": True,
                    }
                    for start, end, section in sections
                ],
            }
        ],
    }


def send_qualification_notification(show_id: int) -> None:
    _send_notification(_notification_payload(show_id))


def send_running_order_notification(show_id: int) -> None:
    _send_notification(_running_order_payload(show_id))


def send_final_results_notification(show_id: int) -> None:
    _send_notification(_final_results_payload(show_id))


def _send_notification(payload: dict) -> None:
    webhook_url = current_app.config.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise DiscordNotificationError("The Discord webhook is not configured")

    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "WorldStage/qualification-notifications",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=current_app.config["DISCORD_WEBHOOK_TIMEOUT"]
        ) as response:
            if response.status not in {200, 204}:
                raise DiscordNotificationError(
                    f"Discord returned HTTP {response.status}"
                )
    except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise DiscordNotificationError(f"Could not reach Discord: {exc}") from exc


def send_qualification_notification_best_effort(show_id: int) -> None:
    try:
        send_qualification_notification(show_id)
    except DiscordNotificationError:
        current_app.logger.exception(
            "Could not send the qualification notification for show %s", show_id
        )


def send_running_order_notification_best_effort(show_id: int) -> None:
    try:
        send_running_order_notification(show_id)
    except DiscordNotificationError:
        current_app.logger.exception(
            "Could not send the running-order notification for show %s", show_id
        )


def send_final_results_notification_best_effort(show_id: int) -> None:
    try:
        send_final_results_notification(show_id)
    except DiscordNotificationError:
        current_app.logger.exception(
            "Could not send the final-results notification for show %s", show_id
        )
