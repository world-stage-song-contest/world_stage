def test_details_page_shows_both_recap_snippets(client, db):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO song (
                country_id, year_id, title, artist, is_placeholder,
                snippet_start, snippet_end, snippet2_start, snippet2_end
            )
            VALUES ('US', 2025, 'Two Recaps', 'Test Artist', false, 30, 50, 90, 100)
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
