def test_details_page_shows_both_recap_snippets(client, db):
    with db.cursor() as cur:
        cur.execute(
            """
            WITH inserted AS (
                INSERT INTO song (country_id, year_id) VALUES ('US', 2025) RETURNING id
            ) INSERT INTO song_data (
                song_id, title, artist,
                snippet_start, snippet_end, snippet2_start, snippet2_end
            ) SELECT id, 'Two Recaps', 'Test Artist', 30, 50, 90, 100 FROM inserted
            """
        )
    db.commit()

    response = client.get("/country/us/2025", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert "<th>First recap</th>" in response.text
    assert 'data-seconds="30">0:30</a>' in response.text
    assert 'data-seconds="50">0:50</a>' in response.text
    assert "<th>Second recap</th>" in response.text
    assert '"label": "Second"' in response.text
    assert '"is_second": true' in response.text
    assert ".recap-marker.recap-marker-second" in response.text
    assert "'recap-marker recap-marker-second'" in response.text
    assert 'data-seconds="90">1:30</a>' in response.text
    assert 'data-seconds="100">1:40</a>' in response.text


def test_details_page_offers_direct_download(client, db):
    media_url = "https://media.world-stage.org/song.mp4?version=2"
    with db.cursor() as cur:
        cur.execute(
            """
            WITH inserted AS (
                INSERT INTO song (country_id, year_id) VALUES ('US', 2025) RETURNING id
            ) INSERT INTO song_data (song_id, title, artist, video_link)
              SELECT id, 'Downloadable', 'Test Artist', %s FROM inserted
            """,
            (media_url,),
        )
    db.commit()

    response = client.get("/country/us/2025", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert f'href="{media_url}&amp;download=1" download' in response.text
    assert "Download" in response.text


def test_details_page_does_not_offer_download_without_media(client, db):
    with db.cursor() as cur:
        cur.execute(
            """
            WITH inserted AS (
                INSERT INTO song (country_id, year_id) VALUES ('US', 2025) RETURNING id
            ) INSERT INTO song_data (song_id, title, artist, video_link)
              SELECT id, 'Missing', 'Test Artist', NULL FROM inserted
            """
        )
    db.commit()

    response = client.get("/country/us/2025", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert "download=1" not in response.text


def test_details_page_attaches_subtitles_to_direct_media(client, db):
    media_url = "https://media.world-stage.org/song.mp4"
    vtt_url = "https://media.world-stage.org/song.vtt"
    with db.cursor() as cur:
        cur.execute(
            """
            WITH inserted AS (
                INSERT INTO song (country_id, year_id) VALUES ('US', 2025) RETURNING id
            ) INSERT INTO song_data (
                song_id, title, artist, video_link, vtt_link
            ) SELECT id, 'Captioned', 'Test Artist', %s, %s FROM inserted
            """,
            (media_url, vtt_url),
        )
    db.commit()

    response = client.get("/country/us/2025", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert 'crossorigin="anonymous"' in response.text
    assert (
        f'<track kind="subtitles" src="{vtt_url}" srclang="en" '
        'label="Subtitles" default>'
    ) in response.text
