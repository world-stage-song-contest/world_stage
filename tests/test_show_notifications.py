from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, available_timezones

from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb

from world_stage.show_notifications import (
    GEOGRAPHIC_TIMEZONE_REGIONS,
    UTC_TIMEZONE,
    notification_time,
    send_due_notifications,
    sensible_timezones,
)


@given(timezone_name=st.sampled_from(sensible_timezones()))
def test_user_facing_timezones_are_geographic_or_utc(timezone_name):
    assert timezone_name == UTC_TIMEZONE or timezone_name.startswith(
        tuple(f"{region}/" for region in GEOGRAPHIC_TIMEZONE_REGIONS)
    )
    assert ZoneInfo(timezone_name).key == timezone_name


@settings(max_examples=100)
@given(
    show_start=st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2040, 12, 31, 23, 59),
        timezones=st.just(UTC),
    ),
    timezone_name=st.sampled_from(sorted(available_timezones())),
)
def test_notification_time_obeys_the_users_local_calendar(show_start, timezone_name):
    user_timezone = ZoneInfo(timezone_name)
    local_start = show_start.astimezone(user_timezone)

    reminder = notification_time(show_start, timezone_name).astimezone(user_timezone)

    expected_date = local_start.date() - (
        timedelta(days=1) if local_start.time() < time(12) else timedelta()
    )
    assert reminder.date() == expected_date
    assert reminder.time() == time(8)
    assert reminder < show_start


def test_show_delivery_follows_the_union_of_enabled_categories(app, db, configured_email):
    @settings(max_examples=20, deadline=None)
    @given(
        bob_participating=st.booleans(),
        bob_all=st.booleans(),
        bob_voted=st.booleans(),
        carol_participating=st.booleans(),
        carol_all=st.booleans(),
    )
    def property_test(bob_participating, bob_all, bob_voted, carol_participating, carol_all):
        with db.cursor() as cursor:
            cursor.executemany(
                "UPDATE account SET settings = %s WHERE id = %s",
                [
                    (
                        Jsonb(
                            {
                                "notifications": {
                                    "show_reminders": {
                                        "timezone": "Europe/Warsaw",
                                        "participating_shows": bob_participating,
                                        "all_shows": bob_all,
                                    }
                                }
                            }
                        ),
                        2,
                    ),
                    (
                        Jsonb(
                            {
                                "notifications": {
                                    "show_reminders": {
                                        "timezone": "Europe/Warsaw",
                                        "participating_shows": carol_participating,
                                        "all_shows": carol_all,
                                    }
                                }
                            }
                        ),
                        3,
                    ),
                ],
            )
        db.execute(
            "DELETE FROM show_email_notification_delivery WHERE show_id = %s",
            (show_id,),
        )
        db.execute(
            "DELETE FROM vote_set WHERE voter_id = 2 AND show_id = %s",
            (show_id,),
        )
        if bob_voted:
            db.execute(
                """
                INSERT INTO vote_set (voter_id, show_id, result_mode)
                VALUES (2, %s, 'official')
                """,
                (show_id,),
            )
        db.commit()
        app.extensions["mail_outbox"].clear()

        job_time = datetime(2026, 9, 1, 7, tzinfo=UTC)
        delivered = send_due_notifications(job_time)

        expected = set()
        if bob_all or (bob_participating and not bob_voted):
            expected.add("bob-notify@example.test")
        if carol_all:
            expected.add("carol-notify@example.test")
        assert {message["To"] for message in app.extensions["mail_outbox"]} == expected
        assert delivered == len(expected)

        # Re-running the job is observable as a no-op for every preference mix.
        assert send_due_notifications(job_time) == 0
        assert len(app.extensions["mail_outbox"]) == len(expected)

    with app.app_context():
        original_accounts = {
            row["id"]: (row["email"], row["settings"])
            for row in db.execute("SELECT id, email, settings FROM account WHERE id IN (2, 3)")
        }
        db.execute("UPDATE account SET email = 'bob-notify@example.test' WHERE id = 2")
        db.execute("UPDATE account SET email = 'carol-notify@example.test' WHERE id = 3")
        show_id = db.execute(
            """
                INSERT INTO show (year_id, show_type, show_number, date)
                VALUES (2025, 'sf', 99, TIMESTAMPTZ '2026-09-01 17:30:00+00')
                ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL
                DO UPDATE SET date = EXCLUDED.date
                RETURNING id
                """
        ).fetchone()["id"]
        song_id = db.execute(
            """
                INSERT INTO song (country_id, year_id, entry_number)
                VALUES ('US', 2025, 99)
                ON CONFLICT (year_id, country_id, entry_number)
                DO UPDATE SET main_participant = true
                RETURNING id
                """
        ).fetchone()["id"]
        db.execute(
            """
                INSERT INTO song_data (
                    song_id, country_id, year_id, entry_number, submitter_id,
                    title, artist_credit_set_id
                ) VALUES (
                    %s, 'US', 2025, 99, 2, 'Reminder entry',
                    test_artist_credit('Reminder artist')
                )
                """,
            (song_id,),
        )
        db.execute(
            """
                INSERT INTO song_show (song_id, show_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
            (song_id, show_id),
        )
        db.commit()
        try:
            property_test()
        finally:
            db.execute(
                "DELETE FROM show_email_notification_delivery WHERE show_id = %s", (show_id,)
            )
            db.execute("DELETE FROM vote_set WHERE show_id = %s", (show_id,))
            db.execute("DELETE FROM song_show WHERE show_id = %s", (show_id,))
            db.execute("DELETE FROM show WHERE id = %s", (show_id,))
            db.execute("DELETE FROM song_data WHERE song_id = %s", (song_id,))
            db.execute("DELETE FROM song WHERE id = %s", (song_id,))
            with db.cursor() as cursor:
                cursor.executemany(
                    "UPDATE account SET email = %s, settings = %s WHERE id = %s",
                    [
                        (email, Jsonb(settings), account_id)
                        for account_id, (email, settings) in original_accounts.items()
                    ],
                )
            db.commit()
