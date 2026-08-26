import datetime
from collections import defaultdict
from typing import Any

from flask import Blueprint, make_response, request, url_for

from ..db import fetchone, get_db
from ..utils import (
    ballot_rule_errors,
    dt_now,
    format_timedelta,
    get_ballot_entry_rules,
    get_countries,
    get_show_id,
    get_show_lineup,
    get_user_id_from_session,
    get_user_submission_countries,
    get_vote_count_for_show,
    render_template,
    require_user,
)

bp = Blueprint("vote", __name__, url_prefix="/vote")


def _apply_ballot_rule_errors(
    errors: list[str],
    invalid: list[int],
    votes: dict[int, int],
    rules,
) -> None:
    for kind, reason, _song_id, score in ballot_rule_errors(votes, rules):
        if kind == "forbidden":
            if reason == "owner":
                message = f"You cannot vote for your own song ({score} points)."
            elif reason == "flag_and_owner":
                message = (
                    "You cannot vote for your own song or the entry represented by "
                    f"your voting flag ({score} points)."
                )
            else:
                message = (
                    "You cannot vote for the entry represented by your voting flag "
                    f"({score} points)."
                )
            if message not in errors:
                errors.append(message)
            if score is not None:
                invalid.append(score)
        else:
            message = f"The entry represented by your voting flag must receive {score} point."
            if message not in errors:
                errors.append(message)
            if score is not None:
                invalid.append(score)


def update_votes(
    voter_id, nickname, country_id, point_system_id, votes, show_id
) -> tuple[bool, str]:
    db = get_db()
    cursor = db.cursor()

    session_id = request.cookies.get("session")
    user_data = get_user_id_from_session(session_id)
    user_id = None
    if user_data:
        user_id = user_data[0]

    cursor.execute(
        """
        SELECT id, ip_address FROM vote_set
        WHERE voter_id = %s AND show_id = %s AND result_mode = 'official'
        """,
        (voter_id, show_id),
    )
    vote_set_data = cursor.fetchone()
    if not vote_set_data:
        return False, "Votes not found"

    vote_set_id = vote_set_data["id"]
    ip_addr = vote_set_data["ip_address"]

    if voter_id != user_id and ip_addr != request.remote_addr:
        return False, "IP addresses don't match. Log in or use the same device to vote."

    cursor.execute(
        """
        UPDATE vote_set SET nickname = %s, country_id = %s, ip_address = %s
        WHERE id = %s
    """,
        (nickname, country_id or "XX", request.remote_addr, vote_set_id),
    )

    cursor.execute("UPDATE vote SET song_id = NULL WHERE vote_set_id = %s", (vote_set_id,))

    for score, song_id in votes.items():
        cursor.execute(
            """
            UPDATE vote
            SET song_id = %s
            WHERE vote_set_id = %s AND score = %s
        """,
            (song_id, vote_set_id, score),
        )

    return True, "updated"


def add_votes(username, nickname, country_id, show_id, point_system_id, votes) -> tuple[bool, str]:
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id FROM account WHERE LOWER(username) = LOWER(%s)", (username,))
    voter_id_data = cursor.fetchone()
    if not voter_id_data:
        return (
            False,
            f"User with name '{username}' not found. Ensure that you entered your username into "
            "the Voter Name field and your display name into the Display Name field.",
        )
    voter_id = voter_id_data["id"]

    cursor.execute(
        """
        SELECT id FROM vote_set
        WHERE voter_id = %s AND show_id = %s AND result_mode = 'official'
        """,
        (voter_id, show_id),
    )
    existing_vote_set = cursor.fetchone()

    res = True
    if not existing_vote_set:
        cursor.execute(
            """
            INSERT INTO vote_set
                (voter_id, show_id, country_id, nickname, ip_address, created_at, result_mode)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 'official')
            RETURNING id
            """,
            (voter_id, show_id, country_id or "XX", nickname, request.remote_addr),
        )
        vote_set_id = fetchone(cursor)["id"]
        for score, song_id in votes.items():
            cursor.execute(
                "INSERT INTO vote (vote_set_id, song_id, score) VALUES (%s, %s, %s)",
                (vote_set_id, song_id, score),
            )
        action = "added"
    else:
        res, action = update_votes(voter_id, nickname, country_id, point_system_id, votes, show_id)

    db.commit()

    return res, action


@bp.get("/")
def index():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT show.id, show.show_name AS name,
               COALESCE(national_final.short_name || '-', '') || show.short_name AS short_name,
               show.year_id AS year, show.voting_opens, show.voting_closes,
               show.predictions_close, show.date,
               year.special_name, year.special_short_name,
               national_final.id AS national_final_id,
               national_final.name AS national_final_name
        FROM show
        JOIN show_types ON show_types.id = show.show_type
        LEFT JOIN year ON year.id = show.year_id
        LEFT JOIN national_final ON national_final.id = show.national_final_id
        WHERE voting_opens <= CURRENT_TIMESTAMP
          AND (voting_closes IS NULL OR voting_closes >= CURRENT_TIMESTAMP)
          AND (national_final.id IS NULL OR national_final.status = 'voting')
        ORDER BY (national_final.id IS NOT NULL),
                 (show.year_id < 0), show.year_id DESC,
                 national_final.id NULLS FIRST, national_final.name,
                 show_types.sort_order, show.show_number NULLS FIRST, show.id
    """)

    year_sections: dict[tuple[int | None, int | None], dict] = {}
    special_sections: dict[tuple[int | None, int | None], dict] = {}
    for row in cursor.fetchall():
        left = None
        if row["voting_closes"]:
            left = row["voting_closes"] - dt_now()
        pred_deadline = row["predictions_close"] or row["voting_closes"]
        predictions_open = not pred_deadline or pred_deadline >= dt_now()
        if row["special_short_name"]:
            parent_name = row["special_name"]
            url_short = f"{row['special_short_name']}-{row['short_name']}"
            sections = special_sections
        elif row["year"] is not None:
            parent_name = str(row["year"])
            url_short = f"{row['year']}-{row['short_name']}"
            sections = year_sections
        else:
            parent_name = "Other"
            url_short = row["short_name"]
            sections = year_sections
        section_key = (row["year"], row["national_final_id"])
        section_name = parent_name
        if row["national_final_name"]:
            section_name = f"{parent_name}: {row['national_final_name']}"
        section = sections.setdefault(
            section_key,
            {"name": section_name, "shows": []},
        )
        section["shows"].append(
            {
                "id": row["id"],
                "name": row["name"],
                "short_name": url_short,
                "voting_opens": row["voting_opens"],
                "voting_closes": row["voting_closes"],
                "predictions_open": predictions_open,
                "left": format_timedelta(left),
                "date": row["date"],
            }
        )
    return render_template(
        "vote/index.html",
        year_sections=list(year_sections.values()),
        special_sections=list(special_sections.values()),
    )


@bp.get("/<show>")
@require_user(message="Please log in to vote")
def vote(show: str, user: tuple[int, str]):
    nickname = None
    country = ""
    country_id = ""

    selected: dict[int, dict[str, Any]] = defaultdict(dict)

    show_data = get_show_id(show)

    if not show_data or not show_data.id:
        return render_template("error.html", error="Show not found"), 404

    if (
        show_data.national_final_id is not None
        and show_data.national_final_status != "voting"
        or show_data.voting_opens
        and show_data.voting_opens > dt_now()
        or show_data.voting_closes
        and show_data.voting_closes < dt_now()
    ):
        return render_template("error.html", error="Voting is closed"), 400

    db = get_db()
    cursor = db.cursor()

    _, username = user

    vote_set_id = None
    countries = []
    if username:
        cursor.execute("SELECT id FROM account WHERE LOWER(username) = LOWER(%s)", (username,))
        user_id = cursor.fetchone()
        if user_id:
            countries = get_user_submission_countries(user_id["id"], show_data.year, main_only=True)
            cursor.execute(
                """
                SELECT vote_set.id AS vsid, vote_set.nickname, vote_set.country_id AS cid
                FROM vote_set
                JOIN account ON vote_set.voter_id = account.id
                WHERE LOWER(account.username) = LOWER(%s) AND vote_set.show_id = %s
                  AND vote_set.result_mode = 'official'
            """,
                (username, show_data.id),
            )
            vs_row = cursor.fetchone()
            if vs_row:
                vote_set_id = vs_row["vsid"]
                nickname = vs_row["nickname"]
                country_id = vs_row["cid"]

    if countries and not country_id:
        country_id = countries[0].cc
    if not countries:
        countries = get_countries()

    if vote_set_id:
        cursor.execute(
            """
            SELECT song_id, score, country.id AS cc FROM vote
            JOIN current_song AS song ON vote.song_id = song.id
            JOIN country ON song.country_id = country.id
            WHERE vote_set_id = %s
        """,
            (vote_set_id,),
        )
        for row in cursor.fetchall():
            selected[row["score"]]["sid"] = row["song_id"]
            selected[row["score"]]["cc"] = row["cc"]

    all_songs = get_show_lineup(show_data.year, show_data.short_name) or []
    song_rules = get_ballot_entry_rules(
        show_data.id,
        "official",
        user[0],
        country_id or None,
        [song.id for song in all_songs],
    )
    songs = [song for song in all_songs if song_rules[song.id].kind != "FORBIDDEN"]
    songs_by_id = {song.id: song for song in songs}
    for song_id, rule in song_rules.items():
        if rule.kind == "FORCED" and rule.required_score is not None:
            song = songs_by_id[song_id]
            selected[rule.required_score] = {
                "sid": song_id,
                "cc": song.country.cc,
            }

    return render_template(
        "vote/vote.html",
        songs=all_songs,
        song_rules=song_rules,
        forced_song_by_score={
            rule.required_score: song_id
            for song_id, rule in song_rules.items()
            if rule.kind == "FORCED" and rule.required_score is not None
        },
        points=show_data.points,
        selected=selected,
        username=username,
        nickname=nickname,
        country=country,
        year=show_data.year,
        show_name=show_data.name,
        show_start=show_data.date,
        short_name=show_data.short_name,
        show=show,
        selected_country=country_id,
        countries=countries,
        vote_count=get_vote_count_for_show(show_data.id),
        rules_url=url_for("vote.ballot_rules", show=show),
    )


@bp.get("/<show>/rules")
@require_user(message="Please log in to vote")
def ballot_rules(show: str, user: tuple[int, str]):
    show_data = get_show_id(show)
    if not show_data or not show_data.id:
        return {"error": "Show not found"}, 404
    if (
        show_data.national_final_id is not None
        and show_data.national_final_status != "voting"
        or show_data.voting_opens
        and show_data.voting_opens > dt_now()
        or show_data.voting_closes
        and show_data.voting_closes < dt_now()
    ):
        return {"error": "Voting is closed"}, 400

    voter_id, _ = user
    country_id = request.args.get("country") or None
    countries = get_user_submission_countries(voter_id, show_data.year)
    submitted_country_ids = {country.cc for country in countries}
    if submitted_country_ids and country_id not in submitted_country_ids:
        return {"error": "Country is not available to this voter"}, 400
    if not submitted_country_ids:
        valid_country_ids = {country.cc for country in get_countries()}
        if country_id not in valid_country_ids:
            return {"error": "Country is not available to this voter"}, 400

    songs = get_show_lineup(show_data.year, show_data.short_name) or []
    rules = get_ballot_entry_rules(
        show_data.id,
        "official",
        voter_id,
        country_id,
        [song.id for song in songs],
    )
    return {
        "rules": {
            str(song_id): {
                "kind": rule.kind,
                "reason": rule.reason,
                "required_score": rule.required_score,
            }
            for song_id, rule in rules.items()
        }
    }


@bp.post("/<show>")
@require_user(message="Please log in to vote")
def vote_post(show: str, user: tuple[int, str]):
    votes = {}
    invalid = []
    username = ""
    nickname = ""

    show_data = get_show_id(show)

    if not show_data or not show_data.id:
        return render_template("error.html", error="Show not found"), 404

    if (
        show_data.national_final_id is not None
        and show_data.national_final_status != "voting"
        or show_data.voting_opens
        and show_data.voting_opens > dt_now()
        or show_data.voting_closes
        and show_data.voting_closes < dt_now()
    ):
        return render_template("error.html", error="Voting is closed"), 400

    songs = get_show_lineup(show_data.year, show_data.short_name) or []

    errors = []

    voter_id, username = user
    nickname = request.form["nickname"]
    nickname = nickname.strip()
    country_id: str | None = request.form["country"]
    if not country_id:
        country_id = None

    country_codes = []
    country_names = []

    countries = get_user_submission_countries(voter_id, show_data.year)
    country_codes = [c.cc for c in countries]
    country_names = [c.name for c in countries]

    if country_codes and country_id not in country_codes:
        errors.append(
            f"You can only vote as one of the countries you submitted: ({', '.join(country_names)})"
        )
        country_id = None

    missing = []
    for point in show_data.points:
        id_str = request.form.get(f"pts-{point}")
        if not id_str:
            missing.append(point)
            continue
        try:
            song_id = int(id_str)
        except ValueError:
            errors.append(f"Invalid song for {point} points.")
            invalid.append(point)
            continue
        votes[point] = song_id

    if missing:
        errors.append(f"Missing votes for {', '.join(map(str, missing))} points.")
        invalid.extend(missing)

    invalid_votes: dict[int, list[int]] = defaultdict(list)
    for point, song_id in votes.items():
        invalid_votes[song_id].append(point)

    invalid_votes = {k: v for k, v in invalid_votes.items() if len(v) > 1}
    invalid.extend(item for sublist in invalid_votes.values() for item in sublist)

    if invalid_votes:
        dupes = "; ".join(f"{', '.join(map(str, v))} points" for v in invalid_votes.values())
        errors.append(f"Duplicate votes: {dupes}")

    songs_by_id = {s.id: s for s in songs}
    song_rules = get_ballot_entry_rules(
        show_data.id,
        "official",
        voter_id,
        country_id,
        list(songs_by_id),
    )
    for point, song_id in votes.items():
        if song_id not in songs_by_id:
            errors.append(f"Invalid song for {point} points.")
            invalid.append(point)
    _apply_ballot_rule_errors(errors, invalid, votes, song_rules)

    if not errors:
        res, action = add_votes(
            username, nickname or None, country_id, show_data.id, show_data.point_system_id, votes
        )
        if res:
            resp = make_response(
                render_template("vote/success.html", action=action, what="vote", what_act="voting")
            )
            resp.set_cookie("username", username, max_age=datetime.timedelta(days=30))
            return resp
        else:
            return render_template("error.html", error=action)

    selected: dict[int, dict[str, Any]] = defaultdict(dict)
    for point, song_id in votes.items():
        selected[point]["sid"] = song_id
        song = songs_by_id.get(song_id)
        if song:
            selected[point]["cc"] = song.country.cc

    return render_template(
        "vote/vote.html",
        songs=songs,
        song_rules=song_rules,
        forced_song_by_score={
            rule.required_score: song_id
            for song_id, rule in song_rules.items()
            if rule.kind == "FORCED" and rule.required_score is not None
        },
        points=show_data.points,
        errors=errors,
        selected=selected,
        invalid=invalid,
        username=username,
        nickname=nickname,
        year=show_data.year,
        show_name=show_data.name,
        show_start=show_data.date,
        show=show,
        selected_country=country_id,
        countries=countries or get_countries(),
        rules_url=url_for("vote.ballot_rules", show=show),
    )


@bp.get("/<show>/predict")
@require_user(redirect_to_login=True)
def predict(show: str, user: tuple[int, str]):
    user_id, _ = user

    show_data = get_show_id(show)
    if not show_data or not show_data.id:
        return render_template("error.html", error="Show not found"), 404

    # Predictions close at predictions_close if set, otherwise fall back to voting_closes.
    pred_deadline = show_data.predictions_close or show_data.voting_closes
    if (
        show_data.voting_opens
        and show_data.voting_opens > dt_now()
        or pred_deadline
        and pred_deadline < dt_now()
    ):
        return render_template("error.html", error="Predictions are closed for this show"), 400

    songs = get_show_lineup(show_data.year, show_data.short_name)
    if not songs:
        return render_template("error.html", error="No songs found for this show"), 404

    db = get_db()
    cursor = db.cursor()

    # Load existing prediction and re-sort songs by previously predicted position
    cursor.execute(
        """
        SELECT id FROM prediction_set
        WHERE user_id = %s AND show_id = %s
    """,
        (user_id, show_data.id),
    )
    pred_set = cursor.fetchone()

    has_existing = False
    if pred_set:
        cursor.execute(
            """
            SELECT song_id, position FROM prediction
            WHERE set_id = %s
        """,
            (pred_set["id"],),
        )
        existing = {row["song_id"]: row["position"] for row in cursor.fetchall()}
        if existing:
            has_existing = True
            songs = sorted(songs, key=lambda s: existing.get(s.id, len(songs) + 1))  # type: ignore

    cursor.execute(
        """
        SELECT COUNT(*) AS count FROM prediction_set WHERE show_id = %s
    """,
        (show_data.id,),
    )
    prediction_count = fetchone(cursor)["count"]

    return render_template(
        "vote/predict.html",
        songs=songs,
        show=show,
        show_name=show_data.name,
        year=show_data.year,
        prediction_count=prediction_count,
        has_existing=has_existing,
        dtf=show_data.primary_qualifiers,
        sc=show_data.total_qualifiers - show_data.primary_qualifiers,
    )


@bp.post("/<show>/predict")
@require_user(redirect_to_login=True)
def predict_post(show: str, user: tuple[int, str]):
    user_id, _ = user

    show_data = get_show_id(show)
    if not show_data or not show_data.id:
        return render_template("error.html", error="Show not found"), 404

    pred_deadline = show_data.predictions_close or show_data.voting_closes
    if (
        show_data.voting_opens
        and show_data.voting_opens > dt_now()
        or pred_deadline
        and pred_deadline < dt_now()
    ):
        return render_template("error.html", error="Predictions are closed for this show"), 400

    songs = get_show_lineup(show_data.year, show_data.short_name)
    if not songs:
        return render_template("error.html", error="No songs found for this show"), 404

    valid_song_ids = {s.id for s in songs}
    n_songs = len(songs)

    # Parse form: field name = song id (int), field value = predicted position (int)
    data: list[dict] = []
    positions_seen: set[int] = set()
    errors: list[str] = []

    for key, value in request.form.items():
        try:
            song_id = int(key)
            position = int(value)
        except ValueError:
            continue  # skip submit button or any non-integer field

        if song_id not in valid_song_ids:
            errors.append(f"Unrecognised song (id {song_id}).")
            continue
        if position < 1 or position > n_songs:
            errors.append(f"Position {position} is out of range.")
            continue
        if position in positions_seen:
            errors.append(f"Duplicate position {position}.")
            continue

        positions_seen.add(position)
        data.append({"sid": song_id, "pos": position})

    if len(data) != n_songs:
        errors.append(f"Expected {n_songs} predictions, got {len(data)}. Please rank every song.")

    if errors:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS count FROM prediction_set WHERE show_id = %s", (show_data.id,)
        )
        prediction_count = fetchone(cursor)["count"]
        return render_template(
            "vote/predict.html",
            songs=songs,
            show=show,
            show_name=show_data.name,
            year=show_data.year,
            prediction_count=prediction_count,
            has_existing=False,
            errors=errors,
            dtf=show_data.primary_qualifiers,
            sc=show_data.total_qualifiers - show_data.primary_qualifiers,
        ), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        INSERT INTO prediction_set (user_id, show_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, show_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        RETURNING id
    """,
        (user_id, show_data.id),
    )
    prediction_set_id = fetchone(cursor)["id"]

    cursor.execute("DELETE FROM prediction WHERE set_id = %s", (prediction_set_id,))
    was_update = cursor.rowcount > 0

    cursor.executemany(
        """
        INSERT INTO prediction (set_id, song_id, position)
        VALUES (%(psid)s, %(sid)s, %(pos)s)
    """,
        [{"psid": prediction_set_id, **item} for item in data],
    )

    db.commit()

    action = "updated" if was_update else "submitted"
    return render_template(
        "vote/success.html", action=action, what="prediction", what_act="predicting"
    )
