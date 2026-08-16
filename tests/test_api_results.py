"""Tests for published show results API endpoints."""

import uuid


def _result(response):
    return response.get_json()["result"]


def _seed_results_show(db, *, status='full', year_status='closed', closes='past'):
    close_expression = (
        "CURRENT_TIMESTAMP - INTERVAL '1 hour'"
        if closes == 'past'
        else "CURRENT_TIMESTAMP + INTERVAL '1 hour'"
    )
    with db.cursor() as cursor:
        cursor.execute("UPDATE year SET status = %s WHERE id = 2024", (year_status,))
        cursor.execute(
            "INSERT INTO show_status (name) VALUES ('full') ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            "INSERT INTO show_status (name) VALUES ('partial') ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            "INSERT INTO show_status (name) VALUES ('draw') ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            "INSERT INTO point_system (id, number) VALUES (20, 1) ON CONFLICT DO NOTHING"
        )
        cursor.execute(
            """
            INSERT INTO point (id, point_system_id, place, score)
            VALUES (201, 20, 1, 12), (202, 20, 2, 10)
            ON CONFLICT DO NOTHING
            """
        )
        cursor.execute(
            """
            INSERT INTO show (year_id, point_system_id, show_type, status)
            VALUES (2024, 20, 'f', 'full')
            ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL
            DO UPDATE SET point_system_id = EXCLUDED.point_system_id
            RETURNING id
            """
        )
        target_show_id = cursor.fetchone()['id']
        cursor.execute(
            f"""
            INSERT INTO show (
                year_id, point_system_id, show_type, show_number,
                voting_opens, voting_closes, status
            )
            VALUES (
                2024, 20, 'sf', 81,
                CURRENT_TIMESTAMP - INTERVAL '2 hours', {close_expression}, %s
            )
            ON CONFLICT (year_id, short_name) WHERE national_final_id IS NULL DO UPDATE
            SET point_system_id = EXCLUDED.point_system_id,
                voting_opens = EXCLUDED.voting_opens,
                voting_closes = EXCLUDED.voting_closes,
                status = EXCLUDED.status
            RETURNING id
            """,
            (status,),
        )
        show_id = cursor.fetchone()['id']
        cursor.execute(
            """
            INSERT INTO show_progression (
                source_show_id, target_show_id, qualifier_count, priority
            ) VALUES (%s, %s, 1, 1)
            ON CONFLICT (source_show_id, target_show_id) DO UPDATE
            SET qualifier_count = EXCLUDED.qualifier_count
            """,
            (show_id, target_show_id),
        )
        song_ids = []
        for country_id, title in (('US', 'Winner'), ('ES', 'Runner-up')):
            cursor.execute(
                """
                INSERT INTO song (country_id, year_id)
                VALUES (%s, 2024)
                RETURNING id
                """,
                (country_id,),
            )
            song_id = cursor.fetchone()['id']
            song_ids.append(song_id)
            cursor.execute(
                """INSERT INTO song_data (song_id, title, artist_credit_set_id)
                   VALUES (%s, %s, test_artist_credit('Artist'))""",
                (song_id, title),
            )
        cursor.executemany(
            "INSERT INTO song_show (song_id, show_id, running_order) VALUES (%s, %s, %s)",
            [(song_id, show_id, position) for position, song_id in enumerate(song_ids, start=1)],
        )
        cursor.execute(
            """
            INSERT INTO vote_set (voter_id, show_id, nickname)
            VALUES (2, %s, 'Bob')
            RETURNING id
            """,
            (show_id,),
        )
        vote_set_id = cursor.fetchone()['id']
        cursor.executemany(
            "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
            [(vote_set_id, song_ids[0], 12), (vote_set_id, song_ids[1], 10)],
        )
    db.commit()


class TestResultsApi:
    def test_full_results_and_detailed_ballots(self, client, db):
        _seed_results_show(db)

        response = client.get('/api/results/2024-sf81')
        assert response.status_code == 200
        data = _result(response)
        assert data['show']['year_status'] == 'closed'
        assert data['access'] == 'full'
        assert [(entry['country_id'], entry['place']) for entry in data['entries']] == [
            ('US', 1),
            ('ES', 2),
        ]
        assert [
            {
                key: entry[key]
                for key in (
                    'points_percentage',
                    'adjusted_points_percentage',
                    'adjusted_max_possible_points',
                    'points_midpoint',
                )
            }
            for entry in data['entries']
        ] == [
            {
                'points_percentage': '100.00',
                'adjusted_points_percentage': '100.00',
                'adjusted_max_possible_points': 12,
                'points_midpoint': '11.000000',
            },
            {
                'points_percentage': '83.33',
                'adjusted_points_percentage': '45.45',
                'adjusted_max_possible_points': 12,
                'points_midpoint': '11.000000',
            },
        ]

        response = client.get('/api/results/2024-sf81/detailed')
        assert response.status_code == 200
        assert _result(response)['voters'][0]['username'] == 'bob'
        assert _result(response)['voters'][0]['votes'][0]['score'] == 12

    def test_partial_results_hide_qualifiers(self, client, db):
        _seed_results_show(db, status='partial')

        response = client.get('/api/results/2024-sf81')
        assert response.status_code == 200
        data = _result(response)
        assert data['access'] == 'partial'
        assert data['qualifiers'] == [{
            'song_id': data['qualifiers'][0]['song_id'],
            'country_id': 'US',
            'target_show_id': data['show']['progressions'][0]['target_show_id'],
            'target_short_name': 'f',
            'special': False,
        }]
        assert [(entry['country_id'], entry['place']) for entry in data['entries']] == [('ES', 2)]

        response = client.get('/api/results/2024-sf81/detailed')
        assert response.status_code == 403

    def test_special_qualifier_is_stored_per_song(self, client, db):
        _seed_results_show(db, status='partial')
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT source.id AS source_id, target.id AS target_id
                FROM show AS source
                JOIN show_progression AS progression
                  ON progression.source_show_id = source.id
                JOIN show AS target ON target.id = progression.target_show_id
                WHERE source.year_id = 2024 AND source.short_name = 'sf81'
                """
            )
            shows = cursor.fetchone()
            cursor.execute(
                "SELECT id FROM current_song WHERE year_id = 2024 AND country_id = 'ES'"
            )
            song_id = cursor.fetchone()['id']
            cursor.execute(
                "INSERT INTO song_show (song_id, show_id) VALUES (%s, %s)",
                (song_id, shows['target_id']),
            )
            cursor.execute(
                """
                INSERT INTO show_qualifier (
                    source_show_id, target_show_id, song_id,
                    qualifier_order, is_special
                ) VALUES (%s, %s, %s, 2, true)
                """,
                (shows['source_id'], shows['target_id'], song_id),
            )
            cursor.execute(
                "SELECT refresh_show_results_for_mode(%s, 'official')",
                (shows['source_id'],),
            )
        db.commit()

        data = _result(client.get('/api/results/2024-sf81'))
        assert [(entry['country_id'], entry['special']) for entry in data['qualifiers']] == [
            ('US', False),
            ('ES', True),
        ]
        assert data['entries'] == []

    def test_special_qualifier_controls_are_separate_from_reveal(self, client, db):
        _seed_results_show(db, status='partial')
        session_id = str(uuid.uuid4())
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO session (user_id, session_id, expires_at)
                VALUES (1, %s, CURRENT_TIMESTAMP + INTERVAL '1 day')
                """,
                (session_id,),
            )
        db.commit()
        client.set_cookie('session', session_id)
        auth = {'Accept': 'application/json'}

        reveal = client.get('/year/2024/sf81/qualifiers', headers=auth)
        assert reveal.status_code == 200
        reveal_data = reveal.get_json()
        assert reveal_data['progressions']
        assert 'candidates' not in reveal_data
        assert 'special_qualifiers' not in reveal_data

        management = client.get(
            '/year/2024/sf81/qualifiers/special', headers=auth
        )
        assert management.status_code == 200
        management_data = management.get_json()
        assert management_data['candidates']
        assert management_data['special_qualifiers'] == []

        with db.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM current_song WHERE year_id = 2024 AND country_id = 'ES'"
            )
            song_id = cursor.fetchone()['id']
            cursor.execute(
                """
                SELECT target_show_id FROM show_progression
                JOIN show ON show.id = show_progression.source_show_id
                WHERE show.year_id = 2024 AND show.short_name = 'sf81'
                """
            )
            target_show_id = cursor.fetchone()['target_show_id']

        response = client.post(
            '/year/2024/sf81/qualifiers/special',
            data={
                'action': 'add',
                'song_id': song_id,
                'target_show_id': target_show_id,
            },
        )
        assert response.status_code == 302
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT is_special FROM show_qualifier
                WHERE source_show_id = (
                    SELECT id FROM show
                    WHERE year_id = 2024 AND short_name = 'sf81'
                ) AND song_id = %s
                """,
                (song_id,),
            )
            assert cursor.fetchone()['is_special'] is True
            cursor.execute(
                "DELETE FROM session WHERE session_id = %s", (session_id,)
            )
        db.commit()

    def test_draw_returns_entries_without_scores(self, client, db):
        _seed_results_show(db, status='draw')

        response = client.get('/api/results/2024-sf81')
        assert response.status_code == 200
        data = _result(response)
        assert data['access'] == 'draw'
        assert data['entries'][0]['title'] == 'Winner'
        assert 'total_points' not in data['entries'][0]

    def test_open_year_blocks_public_results(self, client, db):
        _seed_results_show(db, year_status='open')

        response = client.get('/api/results/2024-sf81')
        assert response.status_code == 403
        assert response.get_json()['error']['description'] == (
            'Results are not published for an open year'
        )
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2024")
        db.commit()
