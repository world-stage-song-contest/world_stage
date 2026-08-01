def _create_song(client, headers, country, title):
    response = client.post(
        "/api/song",
        json={
            "year": 2025,
            "country": country,
            "title": title,
            "artist": "Artist",
            "sources": "https://example.com",
            "languages": [20],
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.get_json()["result"]["id"]


def test_verification_highlights_are_limited_to_upcoming_years(
    client, db, alice_headers
):
    accepted_id = _create_song(client, alice_headers, "ES", "Accepted song")
    rejected_id = _create_song(client, alice_headers, "FR", "Rejected song")

    with db.cursor() as cursor:
        from world_stage.utils.song_revisions import set_song_status

        set_song_status(
            cursor, accepted_id, approval_status="accepted", changed_by=1
        )
        set_song_status(
            cursor, rejected_id, approval_status="rejected", changed_by=1
        )
    db.commit()

    upcoming = client.get("/year/2025", headers={"Accept": "text/html"})
    assert upcoming.status_code == 200
    assert upcoming.text.count("verification-accepted") == 1
    assert upcoming.text.count("verification-rejected") == 1

    try:
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'ongoing' WHERE id = 2025")
        db.commit()

        ongoing = client.get("/year/2025", headers={"Accept": "text/html"})
        assert ongoing.status_code == 200
        assert "verification-accepted" not in ongoing.text
        assert "verification-rejected" not in ongoing.text
    finally:
        with db.cursor() as cursor:
            cursor.execute("UPDATE year SET status = 'open' WHERE id = 2025")
        db.commit()
