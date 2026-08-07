from world_stage.db import get_db
from world_stage.routes.year.scoreboard import _scoreboard_theme
from world_stage.routes.year.themes import qualifier_theme


def test_scoreboard_theme_uses_stored_style_and_null_falls_back(app):
    with app.app_context():
        cursor = get_db().cursor()
        cursor.execute(
            "UPDATE year SET scoreboard_style = 'esc-1997' WHERE id = 2025"
        )
        assert _scoreboard_theme(2025)["scoreboard_script"] == "js/scoreboard-1997.js"
        cursor.execute("UPDATE year SET scoreboard_style = NULL WHERE id = 2025")
        assert _scoreboard_theme(2025)["scoreboard_script"] == "js/scoreboard-default.js"


def test_scoreboard_theme_rejects_unknown_but_allows_any_year_override(app):
    with app.app_context():
        cursor = get_db().cursor()
        cursor.execute(
            "UPDATE year SET scoreboard_style = 'not-a-theme' WHERE id = 2025"
        )
        cursor.execute(
            """
            INSERT INTO year (id, status, host_id, scoreboard_style)
            VALUES (1994, 'closed', 'ES', 'esc-1997')
            ON CONFLICT (id) DO UPDATE SET scoreboard_style = EXCLUDED.scoreboard_style
            """
        )
        assert _scoreboard_theme(2025)["scoreboard_style"] is None
        assert _scoreboard_theme(1994)["scoreboard_style"] == "esc-1997"


def test_qualifier_reveal_uses_the_same_stored_year_theme(app):
    with app.app_context():
        cursor = get_db().cursor()
        cursor.execute(
            "UPDATE year SET scoreboard_style = 'esc-1997' WHERE id = 2025"
        )
        theme = qualifier_theme(2025)
        assert theme["qualifier_style"] == "esc-1997"
        assert theme["qualifier_script"] == "js/qualifiers-1997.js"
        assert "css/qualifiers-1997.css" in theme["qualifier_stylesheets"]

        cursor.execute("UPDATE year SET scoreboard_style = NULL WHERE id = 2025")
        theme = qualifier_theme(2025)
        assert theme["qualifier_style"] is None
        assert theme["qualifier_script"] == "js/qualifiers-default.js"
