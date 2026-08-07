from world_stage.routes import user as user_routes


def test_history_user_list_sorts_case_and_accent_insensitively(app, monkeypatch):
    class Cursor:
        query = ""

        def execute(self, query, params):
            self.query = query
            assert params == (2, 2)

        def fetchall(self):
            return []

    cursor = Cursor()
    monkeypatch.setattr(user_routes, "render_template", lambda template, **context: context)

    with app.test_request_context("/user/bob/votes?view=user"):
        user_routes._votes_by_user(cursor, 2, "bob")

    assert "LOWER(unaccent(account.username)) AS username_sort" in cursor.query
    assert "ORDER BY username_sort, account.username, account.id" in cursor.query


def test_history_filters_country_and_user_lists_to_voted_shows(
    client, db, rendered_templates
):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING")
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (910, 2) ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """
            INSERT INTO point (point_system_id, place, score)
            VALUES (910, 1, 12), (910, 2, 10)
            ON CONFLICT DO NOTHING
            """
        )
        cursor.execute(
            """
            SELECT is_generated = 'ALWAYS' AS generated
            FROM information_schema.columns
            WHERE table_name = 'show' AND column_name = 'short_name'
            """
        )
        if cursor.fetchone()["generated"]:
            cursor.execute(
                """
                INSERT INTO show_types (id, name, sort_order)
                VALUES ('sf', 'Semi-Final', 1)
                ON CONFLICT DO NOTHING
                """
            )
            cursor.execute(
                """
                INSERT INTO show (year_id, point_system_id, show_type, show_number, status)
                VALUES (2024, 910, 'sf', 91, 'full'),
                       (2024, 910, 'sf', 92, 'full')
                RETURNING id, short_name
                """
            )
        else:
            cursor.execute(
                """
                INSERT INTO show (year_id, point_system_id, show_name, short_name, status)
                VALUES (2024, 910, 'History voted show', 'sf91', 'full'),
                       (2024, 910, 'History other show', 'sf92', 'full')
                RETURNING id, short_name
                """
            )
        shows = {row["short_name"]: row["id"] for row in cursor.fetchall()}

        songs = []
        for country, title, submitter_id, show in (
            ("US", "Own entry", 2, "sf91"),
            ("ES", "Shared-show entry", 1, "sf91"),
            ("FR", "Other-show entry", 3, "sf92"),
        ):
            cursor.execute(
                "INSERT INTO song (country_id, year_id) VALUES (%s, 2024) RETURNING id",
                (country,),
            )
            song_id = cursor.fetchone()["id"]
            songs.append((song_id, shows[show]))
            cursor.execute(
                """
                INSERT INTO song_data (song_id, title, artist, submitter_id)
                VALUES (%s, %s, 'Artist', %s)
                """,
                (song_id, title, submitter_id),
            )

        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, 1)",
            songs,
        )
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, country_id, result_mode)
            VALUES (2, %s, 'US', 'official')
            """,
            (shows["sf91"],),
        )
    db.commit()

    country_response = client.get(
        "/user/bob/votes?view=country", headers={"Accept": "text/html"}
    )
    assert country_response.status_code == 200
    assert {country["cc"] for country in rendered_templates[-1][1]["country_list"]} == {
        "ES",
        "US",
    }

    user_response = client.get("/user/bob/votes?view=user", headers={"Accept": "text/html"})
    assert user_response.status_code == 200
    assert [user["id"] for user in rendered_templates[-1][1]["user_list"]] == [1]
