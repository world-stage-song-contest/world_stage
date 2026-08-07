from ...db import get_db

YEAR_THEMES = {
    None: {
        "scoreboard_style": None,
        "scoreboard_stylesheets": (
            "css/font-dseg.css",
            "css/font-eurostile.css",
            "css/scoreboard.css",
            "css/scoreboard-cards.css",
        ),
        "scoreboard_script": "js/scoreboard-default.js",
        "qualifier_style": None,
        "qualifier_stylesheets": (
            "css/qualifiers.css",
            "css/qualifiers-envelopes.css",
        ),
        "qualifier_script": "js/qualifiers-default.js",
    },
    "esc-1997": {
        "scoreboard_style": "esc-1997",
        "scoreboard_stylesheets": (
            "css/font-scoreboard-1997.css",
            "css/scoreboard-1997.css",
        ),
        "scoreboard_script": "js/scoreboard-1997.js",
        "qualifier_style": "esc-1997",
        "qualifier_stylesheets": (
            "css/font-scoreboard-1997.css",
            "css/qualifiers-1997.css",
        ),
        "qualifier_script": "js/qualifiers-1997.js",
    },
}


def _year_theme(year_id: int) -> dict:
    """Resolve a stored year theme to a known, safe set of static assets."""
    cursor = get_db().cursor()
    cursor.execute("SELECT scoreboard_style FROM year WHERE id = %s", (year_id,))
    row = cursor.fetchone()
    style = row["scoreboard_style"] if row else None

    if style not in YEAR_THEMES:
        style = None
    return YEAR_THEMES[style]


def scoreboard_theme(year_id: int) -> dict:
    theme = _year_theme(year_id)
    return {key: value for key, value in theme.items() if key.startswith("scoreboard_")}


def qualifier_theme(year_id: int) -> dict:
    theme = _year_theme(year_id)
    return {key: value for key, value in theme.items() if key.startswith("qualifier_")}
