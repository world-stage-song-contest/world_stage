import smtplib
from datetime import UTC, date, datetime, time, timedelta
from importlib.resources import files
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from flask import current_app

from .db import get_db
from .email import external_url, is_configured, send_email

SHOW_TIMEZONE = ZoneInfo("Europe/Warsaw")
SHOW_START_TIME = time(19, 30)
REMINDER_TIME = time(8)
EARLY_SHOW_CUTOFF = time(12)
GEOGRAPHIC_TIMEZONE_REGIONS = (
    "Africa",
    "America",
    "Antarctica",
    "Arctic",
    "Asia",
    "Atlantic",
    "Australia",
    "Europe",
    "Indian",
    "Pacific",
)
UTC_TIMEZONE = "Etc/UTC"
TimezoneChoice = tuple[str, str]
TimezoneGroup = tuple[str, tuple[TimezoneChoice, ...]]


def sensible_timezones() -> tuple[str, ...]:
    """Return canonical geographic IANA zones from tzdata's location catalogue."""
    prefixes = tuple(f"{region}/" for region in GEOGRAPHIC_TIMEZONE_REGIONS)
    catalogue = files("tzdata.zoneinfo").joinpath("zone1970.tab").read_text(encoding="utf-8")
    geographic = {
        fields[2]
        for line in catalogue.splitlines()
        if line and not line.startswith("#") and len(fields := line.split("\t")) >= 3
    }
    return tuple(
        sorted({UTC_TIMEZONE, *(zone for zone in geographic if zone.startswith(prefixes))})
    )


def grouped_timezones(timezones: tuple[str, ...]) -> tuple[TimezoneGroup, ...]:
    """Group timezone values and readable labels for the settings dropdown."""
    groups: list[TimezoneGroup] = []
    if UTC_TIMEZONE in timezones:
        groups.append(("UTC", ((UTC_TIMEZONE, "UTC"),)))
    for region in GEOGRAPHIC_TIMEZONE_REGIONS:
        prefix = f"{region}/"
        choices = tuple(
            (
                timezone_name,
                timezone_name.removeprefix(prefix).replace("_", " ").replace("/", " / "),
            )
            for timezone_name in timezones
            if timezone_name.startswith(prefix)
        )
        if choices:
            groups.append((region, choices))
    return tuple(groups)


def notification_time(show_date: date, timezone_name: str) -> datetime:
    """Return the UTC instant when a user should be reminded about a show."""
    user_timezone = ZoneInfo(timezone_name)
    show_start = datetime.combine(show_date, SHOW_START_TIME, SHOW_TIMEZONE)
    local_start = show_start.astimezone(user_timezone)
    reminder_date = local_start.date()
    if local_start.time() < EARLY_SHOW_CUTOFF:
        reminder_date -= timedelta(days=1)
    return datetime.combine(reminder_date, REMINDER_TIME, user_timezone).astimezone(UTC)


def valid_timezone(value: str, allowed_timezones: tuple[str, ...] | None = None) -> bool:
    if allowed_timezones is not None and value not in allowed_timezones:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _candidates(now: datetime) -> list[dict]:
    warsaw_date = now.astimezone(SHOW_TIMEZONE).date()
    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT account.id AS account_id,
               account.settings #>>
                   '{notifications,show_reminders,timezone}' AS timezone,
               account.email,
               show.id AS show_id, show.date, show.show_name,
               COALESCE(year.special_name, year.id::text) AS year_name,
               national_final.name AS national_final_name
        FROM account
        CROSS JOIN show
        JOIN year ON year.id = show.year_id
        LEFT JOIN national_final ON national_final.id = show.national_final_id
        WHERE show.date IS NOT NULL
          AND show.date BETWEEN %s AND %s
          AND account.settings #>>
              '{notifications,show_reminders,timezone}' IS NOT NULL
          AND NULLIF(BTRIM(account.email), '') IS NOT NULL
          AND (
              account.settings #>
                  '{notifications,show_reminders,all_shows}' = 'true'::jsonb
              OR (
                  account.settings #>
                      '{notifications,show_reminders,participating_shows}' = 'true'::jsonb
                  AND EXISTS (
                      SELECT 1
                      FROM song_show
                      JOIN current_song ON current_song.id = song_show.song_id
                      WHERE song_show.show_id = show.id
                        AND current_song.submitter_id = account.id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM vote_set
                      WHERE vote_set.show_id = show.id
                        AND vote_set.voter_id = account.id
                        AND vote_set.result_mode = 'official'
                  )
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM show_email_notification_delivery AS delivery
              WHERE delivery.account_id = account.id
                AND delivery.show_id = show.id
          )
        ORDER BY show.date, show.id, account.id
        """,
        (warsaw_date - timedelta(days=1), warsaw_date + timedelta(days=2)),
    )
    return cursor.fetchall()


def send_due_notifications(now: datetime | None = None) -> int:
    """Send all reminders due by ``now`` and return the delivery count."""
    if not is_configured():
        return 0
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)

    db = get_db()
    delivered = 0
    for candidate in _candidates(now):
        due_at = notification_time(candidate["date"], candidate["timezone"])
        show_start_utc = datetime.combine(
            candidate["date"], SHOW_START_TIME, SHOW_TIMEZONE
        ).astimezone(UTC)
        if due_at > now or now >= show_start_utc:
            continue

        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO show_email_notification_delivery (account_id, show_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            RETURNING account_id
            """,
            (candidate["account_id"], candidate["show_id"]),
        )
        if cursor.fetchone() is None:
            db.rollback()
            continue

        local_timezone = ZoneInfo(candidate["timezone"])
        show_start = datetime.combine(candidate["date"], SHOW_START_TIME, SHOW_TIMEZONE).astimezone(
            local_timezone
        )
        event_name = candidate["year_name"]
        if candidate["national_final_name"]:
            event_name += f" {candidate['national_final_name']}"
        label = f"{event_name} {candidate['show_name']}"
        body = (
            f"{label} starts on {show_start.strftime('%Y-%m-%d at %H:%M')} "
            f"({candidate['timezone']}).\n\n"
            f"Visit World Stage: {external_url('/')}\n\n"
            "You can change show notifications in your settings.\n"
        )
        try:
            send_email(candidate["email"].strip(), f"Show reminder: {label}", body)
        except (OSError, RuntimeError, ValueError, smtplib.SMTPException):
            db.rollback()
            current_app.logger.exception(
                "Could not email show notification to account %s",
                candidate["account_id"],
            )
            continue
        db.commit()
        delivered += 1
    return delivered


@click.command("send-show-notifications")
def send_show_notifications_command() -> None:
    """Send due show reminder emails."""
    count = send_due_notifications()
    click.echo(f"Sent {count} show notification{'s' if count != 1 else ''}.")


def init_app(app) -> None:
    if "SHOW_NOTIFICATION_TIMEZONES" not in app.config:
        app.config["SHOW_NOTIFICATION_TIMEZONES"] = sensible_timezones()
    app.cli.add_command(send_show_notifications_command)
