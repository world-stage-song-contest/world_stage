from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st
from test_api_voting import _seed_show_and_songs
from werkzeug.http import http_date


def test_show_voters_lists_official_ballots_then_missing_entry_owners(client, db):
    show_id, songs = _seed_show_and_songs(db)
    names = {1: "alice", 2: "bob", 3: "carol"}
    countries = {"US": "United States", "ES": "Spain", "FR": "France", None: None}

    @settings(max_examples=30, deadline=None)
    @given(
        ballots=st.dictionaries(
            st.integers(1, 3),
            st.tuples(st.integers(0, 3), st.sampled_from(list(countries))),
        ),
        owners=st.lists(st.integers(1, 3), min_size=4, max_size=4),
    )
    def property_test(ballots, owners):
        db.execute("DELETE FROM vote_set WHERE show_id = %s", (show_id,))
        expected = []
        for user_id, (offset, country) in ballots.items():
            created_at = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=offset)
            ballot_id = db.execute(
                """INSERT INTO vote_set (voter_id, show_id, country_id, created_at)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (user_id, show_id, country, created_at),
            ).fetchone()["id"]
            expected.append((created_at, ballot_id, user_id, country))
        for song, owner in zip(songs, owners, strict=True):
            db.execute("UPDATE song_data SET submitter_id = %s WHERE song_id = %s", (owner, song))
        for user_id in names:
            db.execute(
                """INSERT INTO vote_set (voter_id, show_id, result_mode)
                   VALUES (%s, %s, 'revote')""",
                (user_id, show_id),
            )
        db.commit()

        path = "/year/2025/f/voters"
        response = client.get(path, headers={"Accept": "application/json"})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["voters"] == [
            {
                "id": user_id,
                "username": names[user_id],
                "code": country or "XX",
                "country": countries[country],
                "created_at": http_date(created_at),
                "has_entry": user_id in owners,
            }
            for created_at, _, user_id, country in sorted(expected)
        ]
        assert payload["missing_voters"] == [
            {"id": user_id, "username": names[user_id]}
            for user_id in sorted(set(owners) - ballots.keys(), key=names.get)
        ]
        assert client.get(path, headers={"Accept": "text/html"}).status_code == 200

    property_test()


def test_show_voter_visibility_follows_results_publication(client, db, login):
    show_id, _ = _seed_show_and_songs(db)
    db.execute(
        "INSERT INTO show_status (name) VALUES ('none'), ('draw'), ('partial') "
        "ON CONFLICT DO NOTHING"
    )

    db.execute(
        "INSERT INTO year (id, status, special_name, special_short_name) "
        "VALUES (-90025, 'open', 'Voter test', 'voter-test')"
    )
    special_show_id = db.execute(
        "INSERT INTO show (year_id, point_system_id, show_type, status) "
        "VALUES (-90025, 10, 'f', 'full') RETURNING id"
    ).fetchone()["id"]

    @settings(deadline=None)
    @given(
        status=st.sampled_from(["none", "draw", "partial", "full"]),
        user_id=st.sampled_from([None, 1, 2, 3]),
        special=st.booleans(),
    )
    def property_test(status, user_id, special):
        target_id = special_show_id if special else show_id
        db.execute("UPDATE show SET status = %s WHERE id = %s", (status, target_id))
        db.commit()
        client.delete_cookie("session")
        if user_id is not None:
            login(user_id)
        path = "/year/special/voter-test/f/voters" if special else "/year/2025/f/voters"
        response = client.get(path, headers={"Accept": "application/json"})
        expected = 200 if user_id == 1 or status == "full" else 403
        assert response.status_code == expected
        assert client.get(path, headers={"Accept": "text/html"}).status_code == expected

    property_test()
