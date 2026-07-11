"""Tests for published show results API endpoints."""


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
            f"""
            INSERT INTO show (
                year_id, point_system_id, show_name, short_name,
                voting_opens, voting_closes, status, dtf, sc
            )
            VALUES (
                2024, 20, 'Results', 'r',
                CURRENT_TIMESTAMP - INTERVAL '2 hours', {close_expression}, %s, 1, 0
            )
            ON CONFLICT (year_id, show_name) DO UPDATE
            SET point_system_id = EXCLUDED.point_system_id,
                short_name = EXCLUDED.short_name,
                voting_opens = EXCLUDED.voting_opens,
                voting_closes = EXCLUDED.voting_closes,
                status = EXCLUDED.status,
                dtf = EXCLUDED.dtf,
                sc = EXCLUDED.sc
            RETURNING id
            """,
            (status,),
        )
        show_id = cursor.fetchone()['id']
        song_ids = []
        for country_id, title in (('US', 'Winner'), ('ES', 'Runner-up')):
            cursor.execute(
                """
                INSERT INTO song (country_id, year_id, title, artist, is_placeholder)
                VALUES (%s, 2024, %s, 'Artist', false)
                RETURNING id
                """,
                (country_id, title),
            )
            song_ids.append(cursor.fetchone()['id'])
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

        response = client.get('/api/results/2024-r')
        assert response.status_code == 200
        data = _result(response)
        assert data['show']['year_status'] == 'closed'
        assert data['access'] == 'full'
        assert [(entry['country_id'], entry['place']) for entry in data['entries']] == [
            ('US', 1),
            ('ES', 2),
        ]

        response = client.get('/api/results/2024-r/detailed')
        assert response.status_code == 200
        assert _result(response)['voters'][0]['username'] == 'bob'
        assert _result(response)['voters'][0]['votes'][0]['score'] == 12

    def test_partial_results_hide_qualifiers(self, client, db):
        _seed_results_show(db, status='partial')

        response = client.get('/api/results/2024-r')
        assert response.status_code == 200
        data = _result(response)
        assert data['access'] == 'partial'
        assert data['qualifiers'] == [{'song_id': data['qualifiers'][0]['song_id'],
                                       'country_id': 'US', 'type': 'dtf'}]
        assert [(entry['country_id'], entry['place']) for entry in data['entries']] == [('ES', 2)]

        response = client.get('/api/results/2024-r/detailed')
        assert response.status_code == 403

    def test_draw_returns_entries_without_scores(self, client, db):
        _seed_results_show(db, status='draw')

        response = client.get('/api/results/2024-r')
        assert response.status_code == 200
        data = _result(response)
        assert data['access'] == 'draw'
        assert data['entries'][0]['title'] == 'Winner'
        assert 'total_points' not in data['entries'][0]

    def test_open_year_blocks_public_results(self, client, db):
        _seed_results_show(db, year_status='open')

        response = client.get('/api/results/2024-r')
        assert response.status_code == 403
        assert response.get_json()['error']['description'] == (
            'Results are not published for an open year'
        )
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'closed' WHERE id = 2024")
        db.commit()
