import uuid

from hypothesis import given, settings
from hypothesis import strategies as st
from psycopg.types.json import Jsonb
from test_api_voting import _seed_show_and_songs


def _login(client, db, user_id=2):
    session_id = uuid.uuid4()
    db.execute(
        """INSERT INTO session (user_id, session_id, expires_at)
           VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')""",
        (user_id, session_id),
    )
    db.commit()
    client.set_cookie("session", str(session_id))


def _pool(db, show_id, *, total=24, minimum=0, maximum=3, cap=12, floor=1, exhaust=False):
    metadata = {
        "total_points": total, "min_items": minimum, "max_items": maximum,
        "max_points_per_item": cap, "min_points_per_item": floor, "require_all_points": exhaust,
    }
    system_id = db.execute(
        """INSERT INTO point_system (id, kind, metadata)
           SELECT COALESCE(MAX(id), 0) + 1, 'pool', %s FROM point_system RETURNING id""",
        (Jsonb(metadata),),
    ).fetchone()["id"]
    db.execute("UPDATE show SET point_system_id = %s WHERE id = %s", (system_id, show_id))
    db.commit()
    return system_id


def _stored(db, show_id, mode):
    return {
        row["song_id"]: row["score"]
        for row in db.execute(
            """SELECT vote.song_id, vote.score FROM vote
               JOIN vote_set ON vote_set.id = vote.vote_set_id
               WHERE vote_set.show_id = %s AND vote_set.result_mode = %s""",
            (show_id, mode),
        )
    }


def test_pool_web_ballots_replace_all_allocations_including_ties_and_empty_ballots(
    client, db, bob_headers
):
    show_id, songs = _seed_show_and_songs(db)
    system_id = _pool(db, show_id, maximum=None, cap=None)
    _login(client, db)
    db.execute("UPDATE show SET revote_eligible_at = CURRENT_TIMESTAMP WHERE id = %s", (show_id,))
    db.commit()

    @settings(max_examples=20, deadline=None)
    @given(scores=st.lists(st.integers(0, 8), min_size=3, max_size=3), floor=st.integers(1, 8))
    def property_test(scores, floor):
        scores = [score * floor for score in scores]
        db.execute(
            "UPDATE point_system SET metadata = metadata || %s WHERE id = %s",
            (Jsonb({"min_points_per_item": floor, "total_points": 24 * floor}), system_id),
        )
        db.commit()
        expected = {song: score for song, score in zip(songs[1:], scores, strict=True) if score}
        form = {"nickname": "", "country": "US"}
        form.update({f"song-{song}": score for song, score in zip(songs[1:], scores, strict=True)})
        for path, mode in (("/vote/2025-f", "official"), ("/revote/2025/f/vote", "revote")):
            assert client.post(path, data=form).status_code == 200
            assert _stored(db, show_id, mode) == expected
            assert client.get(path).status_code == 200
            results = {
                row["song_id"]: sum(
                    int(score) * count for score, count in row["point_distribution"].items()
                )
                for row in db.execute(
                    """SELECT song_id, total_points, point_distribution FROM country_show_results
                       WHERE show_id = %s AND result_mode = %s""",
                    (show_id, mode),
                )
            }
            assert results == {song: expected.get(song, 0) for song in songs}
        assert client.get("/revote/2025/f?revoters_only=true").status_code == 200
        response = client.get("/api/voting/2025-f", headers=bob_headers)
        result = response.get_json()["result"]
        assert {vote["song_id"]: vote["score"] for vote in result["ballot"]["votes"]} == expected
        assert result["show"]["point_system"]["metadata"]["total_points"] == 24 * floor

    property_test()


def test_pool_api_enforces_limits_and_preserves_saved_votes_on_rejection(client, db, bob_headers):
    show_id, songs = _seed_show_and_songs(db)
    system_id = _pool(db, show_id, total=20, minimum=1, maximum=2, cap=12)
    saved = {song: 10 for song in songs[1:3]}
    baseline = [{"song_id": song, "score": score} for song, score in saved.items()]

    @settings(max_examples=40, deadline=None)
    @given(
        scores=st.lists(st.integers(-1, 25), max_size=3),
        exhaust=st.booleans(),
        maximum=st.sampled_from([None, 2]),
        cap=st.sampled_from([None, 12]),
        floor=st.integers(1, 10),
        target=st.sampled_from(["eligible", "owned", "unknown", "duplicate"]),
    )
    def property_test(scores, exhaust, maximum, cap, floor, target):
        db.execute(
            "UPDATE point_system SET metadata = metadata || %s WHERE id = %s",
            (Jsonb({"require_all_points": exhaust, "max_items": maximum,
                    "max_points_per_item": cap, "min_points_per_item": floor}), system_id),
        )
        db.commit()
        assert (
            client.put(
                "/api/voting/2025-f",
                headers=bob_headers,
                json={"country_id": "US", "votes": baseline},
            ).status_code
            == 200
        )
        chosen = songs[1 : 1 + len(scores)]
        if chosen:
            if target == "owned":
                chosen[0] = songs[0]
            elif target == "unknown":
                chosen[0] = max(songs) + 1000
            elif target == "duplicate" and len(chosen) > 1:
                chosen[-1] = chosen[0]
        valid = (
            len(scores) >= 1
            and (maximum is None or len(scores) <= maximum)
            and all(score >= floor and (cap is None or score <= cap) for score in scores)
            and sum(scores) <= 20
            and (not exhaust or sum(scores) == 20)
            and len(set(chosen)) == len(chosen)
            and all(song in songs[1:] for song in chosen)
        )
        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "country_id": "US",
                "votes": [
                    {"song_id": song, "score": score}
                    for song, score in zip(chosen, scores, strict=True)
                ],
            },
        )
        assert response.status_code == (200 if valid else 400)
        expected = dict(zip(chosen, scores, strict=True)) if valid else saved
        assert _stored(db, show_id, "official") == expected

    property_test()


def test_pool_settings_are_saved_only_when_feasible(client, db):
    _login(client, db, 1)
    db.execute("INSERT INTO show_status (name) VALUES ('none') ON CONFLICT DO NOTHING")
    db.commit()

    @settings(max_examples=25, deadline=None)
    @given(
        total=st.integers(-1, 30),
        minimum=st.one_of(st.none(), st.integers(-1, 6)),
        maximum=st.one_of(st.none(), st.integers(-1, 6)),
        cap=st.one_of(st.none(), st.integers(-1, 15)),
        floor=st.one_of(st.none(), st.integers(-1, 15)),
        omit_limits=st.booleans(),
        exhaust=st.booleans(),
    )
    def property_test(total, minimum, maximum, cap, floor, exhaust, omit_limits):
        form = {
            "show_type": "sf",
            "show_number": "91",
            "national_final_id": "main",
            "point_system_id": "pool",
            "total_points": total,
            "max_items": maximum if maximum is not None else "",
            "max_points_per_item": cap if cap is not None else "",
        }
        if floor is not None:
            form["min_points_per_item"] = floor
        else:
            floor = 1
        if minimum is not None:
            form["min_items"] = minimum
        else:
            minimum = 1
        if omit_limits:
            for key in ("max_items", "max_points_per_item"):
                if form[key] == "":
                    del form[key]
        if exhaust:
            form["require_all_points"] = "on"
        valid = (
            total > 0
            and 1 <= floor <= total
            and (cap is None or cap >= floor)
            and (maximum is None or maximum > 0)
            and minimum >= 0
            and any(
                count >= minimum
                and (maximum is None or count <= maximum)
                and count * floor <= total
                and (not exhaust or count * (cap or total) >= total)
                for count in range(total + 1)
            )
        )
        response = client.post("/admin/manage/2025/create/show", data=form)
        assert response.status_code == (302 if valid else 400)
        row = db.execute(
            """SELECT ps.* FROM point_system ps JOIN show ON show.point_system_id = ps.id
               WHERE show.year_id = 2025 AND show.short_name = 'sf91'"""
        ).fetchone()
        if valid:
            assert (
                row["metadata"]["total_points"],
                row["metadata"]["min_items"],
                row["metadata"]["max_items"],
                row["metadata"]["max_points_per_item"],
                row["metadata"]["min_points_per_item"],
                row["metadata"]["require_all_points"],
            ) == (total, minimum, maximum, cap, floor, exhaust)
            counts = [
                count for count in range(total + 1)
                if count >= minimum
                and (maximum is None or count <= maximum)
                and count * floor <= total
                and (not exhaust or count * (cap or total) >= total)
            ]
            assert row["required_items"] == min(counts)
            assert row["max_score"] == max(
                min(cap or total, total - (count - 1) * floor)
                for count in counts if count > 0
            )
            db.execute("DELETE FROM show WHERE year_id = 2025 AND short_name = 'sf91'")
            db.commit()
        else:
            assert row is None

    property_test()


def test_pool_scoreboard_and_results_preserve_equal_scores(client, db, bob_headers):
    show_id, songs = _seed_show_and_songs(db)
    _login(client, db, 1)

    @settings(max_examples=10, deadline=None)
    @given(score=st.integers(1, 100_000), count=st.integers(0, 3), extra=st.integers(0, 10))
    def property_test(score, count, extra):
        db.execute(
            "DELETE FROM vote WHERE vote_set_id IN (SELECT id FROM vote_set WHERE show_id = %s)",
            (show_id,),
        )
        _pool(
            db,
            show_id,
            total=max(1, score * count),
            minimum=count,
            maximum=max(1, count),
            cap=score + extra,
            floor=score if count else 1,
            exhaust=bool(count),
        )
        expected = {song: score for song in songs[1 : 1 + count]}
        response = client.put(
            "/api/voting/2025-f",
            headers=bob_headers,
            json={
                "country_id": "US",
                "votes": [{"song_id": song, "score": points} for song, points in expected.items()],
            },
        )
        assert response.status_code == 200
        response = client.get("/year/2025/f/scoreboard/votes")
        assert response.status_code == 200
        results = response.get_json()["results"]
        assert list(results.values()) == [{str(song): points for song, points in expected.items()}]
        rows = db.execute(
            """SELECT song_id, total_points, max_pts, max_possible_points
               FROM country_show_results WHERE show_id = %s AND result_mode = 'official'""",
            (show_id,),
        ).fetchall()
        assert {row["song_id"]: row["total_points"] for row in rows} == {
            song: expected.get(song, 0) for song in songs
        }
        maximum_score = min(score, max(1, score * count))
        assert all(
            row["max_pts"] == maximum_score and row["max_possible_points"] == maximum_score
            for row in rows
        )

    property_test()
