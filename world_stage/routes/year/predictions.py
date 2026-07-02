import math
from collections import defaultdict

from ...db import get_db
from ...utils import (
    UserPermissions,
    dt_now,
    get_show_id,
    get_show_songs,
    render_template,
    with_permissions,
)
from .common import bp, get_other_shows, resolve_special


@bp.get("/special/<short_name>/<show>/predictions")
@with_permissions
def special_predictions(short_name: str, show: str, permissions: UserPermissions):
    special_year = resolve_special(short_name)
    if not special_year:
        return render_template("error.html", error="Special not found"), 404

    _year = special_year["id"]
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the predictions yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT prediction_set.id, account.username, prediction_set.created_at
        FROM prediction_set
        JOIN account ON prediction_set.user_id = account.id
        WHERE prediction_set.show_id = %s
        ORDER BY prediction_set.created_at
    """,
        (show_data.id,),
    )
    pred_sets = cursor.fetchall()

    cursor.execute(
        """
        SELECT prediction.set_id, prediction.song_id, prediction.position
        FROM prediction
        JOIN prediction_set ON prediction.set_id = prediction_set.id
        WHERE prediction_set.show_id = %s
    """,
        (show_data.id,),
    )

    pred_by_set: dict[int, dict[int, int]] = defaultdict(dict)
    for row in cursor.fetchall():
        pred_by_set[row["set_id"]][row["song_id"]] = row["position"]

    n_predictors = len(pred_sets)
    n_qualifiers = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)

    is_final = n_qualifiers <= 0
    if is_final:
        odds = _compute_winning_odds(songs, pred_by_set, n_predictors)
    else:
        odds = _compute_qualification_odds(songs, pred_by_set, n_predictors, n_qualifiers)

    predictors: dict[str, dict] = {}
    for ps in pred_sets:
        predictors[ps["username"]] = pred_by_set.get(ps["id"], {})

    pred_points: dict[int, float] = {song.id: 0.0 for song in songs}
    for set_preds in pred_by_set.values():
        for sid, pos in set_preds.items():
            if sid in pred_points:
                pred_points[sid] += 12 * (0.827 ** (pos - 1))

    pred_rank: dict[int, int] = {}
    for rank, song in enumerate(
        sorted(songs, key=lambda s: pred_points[s.id], reverse=True), start=1
    ):
        pred_rank[song.id] = rank

    real_positions: dict[int, int] = {}
    if show_data.status in ("partial", "full"):
        cursor.execute(
            "SELECT song_id, place FROM country_show_results WHERE show_id = %s",
            (show_data.id,),
        )
        real_positions = {row["song_id"]: row["place"] for row in cursor.fetchall()}

    if real_positions:
        songs.sort(key=lambda s: (real_positions.get(s.id) is None, real_positions.get(s.id, 0)))
    else:
        songs.sort(key=lambda s: odds[s.id], reverse=True)

    predicted_class: dict[int, str] = {}
    n_total = len(songs)
    if n_total:
        if is_final:
            predicted_class[songs[0].id] = "first"
            if n_total >= 2:
                predicted_class[songs[1].id] = "second"
            if n_total >= 3:
                predicted_class[songs[2].id] = "third"
            predicted_class[songs[-1].id] = "last"
        else:
            dtf_n = show_data.dtf or 0
            sc_n = show_data.sc or 0
            for i, song in enumerate(songs):
                if i < dtf_n:
                    predicted_class[song.id] = "direct-to-final"
                elif i < dtf_n + sc_n:
                    predicted_class[song.id] = "second-chance"
            predicted_class[songs[-1].id] = "last"

    copy_lines: list[str] = []
    for i, song in enumerate(songs, 1):
        prob = odds[song.id]
        decimal_odds = (1 / prob) if prob > 0 else float("inf")
        pct = prob * 100
        copy_lines.append(f"{i}. {song.country.name}: {decimal_odds:.2f} ({pct:.2f}%)")
    copy_text = "\n".join(copy_lines)

    show_copy = show_data.status != "full"

    predictor_scores: dict[str, int] = {}
    predictor_breakdown: dict[str, list[dict]] = {}
    predictor_penalty: dict[str, dict[int, int]] = {}
    if real_positions:
        predictor_scores, predictor_breakdown, predictor_penalty = (
            _compute_prediction_scores(real_positions, songs, predictors)
        )

    return render_template(
        "year/predictions.html",
        songs=songs,
        predictors=predictors,
        odds=odds,
        predicted_class=predicted_class,
        pred_points=pred_points,
        pred_rank=pred_rank,
        n_predictors=n_predictors,
        n_qualifiers=n_qualifiers,
        copy_text=copy_text,
        show_copy=show_copy,
        show=show,
        show_name=show_data.name,
        year=short_name,
        other_shows=get_other_shows(_year, show),
        special=short_name,
        special_name=special_year["special_name"],
        predictor_scores=predictor_scores,
        predictor_breakdown=predictor_breakdown,
        predictor_penalty=predictor_penalty,
        real_positions=real_positions,
    )

def _compute_qualification_odds(
    songs: list,
    pred_by_set: dict,
    n_predictors: int,
    n_qualifiers: int,
) -> dict[int, float]:
    """
    Compute qualification probability for each song in a semifinal.

    Mean-rank sigmoid model: for each song we compute a smoothed average
    rank across all predictors (with a neutral Beta-style prior pulling
    toward the middle of the leaderboard), then pass it through a logistic
    centred at the qualifier cutoff (N + 0.5). Lower-rank predictions
    (closer to 1) dominate, so a song with many 1st-place picks scores
    very highly even if a few predictors had it much lower.

    Properties of this formula:
    - Songs unanimously ranked in the top N approach 1.0.
    - Songs unanimously ranked outside the top N approach 0.0.
    - A song with mean rank exactly at the cutoff sits at 0.5.
    - 1st-place picks pull the score up much harder than mid-table picks
      pull it down (because the sigmoid saturates well above the cutoff).
    - Sum of probabilities does NOT need to equal N — they are independent
      per-song qualification probabilities.
    """
    n_songs = len(songs)
    if n_predictors == 0 or n_songs == 0 or n_qualifiers <= 0:
        return {song.id: 0.0 for song in songs}

    cutoff = n_qualifiers + 0.5
    # Temperature scales with show size so the transition zone covers
    # roughly the same fraction of the leaderboard regardless of n_songs.
    temperature = max(1.5, n_songs / 8.0)
    # Neutral prior rank — sits at the middle of the field.
    prior_rank = (n_songs + 1) / 2.0
    prior_weight = 1.0

    odds: dict[int, float] = {}
    for song in songs:
        rank_sum = prior_rank * prior_weight
        weight_sum = prior_weight
        for set_preds in pred_by_set.values():
            pos = set_preds.get(song.id, n_songs)
            rank_sum += pos
            weight_sum += 1.0
        mean_rank = rank_sum / weight_sum
        # Logistic centred at cutoff: mean_rank << cutoff → ~1, >> cutoff → ~0.
        odds[song.id] = 1.0 / (1.0 + math.exp((mean_rank - cutoff) / temperature))

    return odds


def _smooth_low_odds(
    odds: dict[int, float],
    threshold: float,
    floor: float,
) -> dict[int, float]:
    """Smooth the low-probability tail.

    Songs at or above ``threshold`` keep their raw values (rescaled
    proportionally so the total still sums to 1.0). Songs below
    ``threshold`` are remapped log-uniformly into ``[floor, threshold]``
    in their original order — preserving relative log-distances within
    the tail while preventing a long flat run pinned at exactly ``floor``.

    The result: every song's odds end up at least ``floor``, the very
    bottom songs sit just above the floor with visibly different values
    (e.g. 0.10%, 0.12%, 0.15%, …) instead of all collapsing to the same
    "1000.00" decimal odds.
    """
    n = len(odds)
    if n == 0:
        return {}
    if n * floor >= 1.0:
        # Floor higher than 1/n — degenerate; fall back to uniform.
        return {sid: 1.0 / n for sid in odds}

    above = {sid: v for sid, v in odds.items() if v >= threshold}
    below = sorted(
        ((sid, v) for sid, v in odds.items() if v < threshold),
        key=lambda x: x[1],
    )

    if not below:
        return dict(odds)

    # Pad the lower edge of the target range so the dead-last doesn't
    # always land on exactly ``floor`` — a constant pad would also look
    # suspiciously identical across shows. The pad blends two
    # data-driven signals so different finals get visibly different
    # floors:
    #   - the favourite's strength (``max_raw``): a strong consensus
    #     leader implies even outsiders shouldn't be quite at the floor.
    #   - the below-threshold tail's geometric mean: when the tail dives
    #     deep (lots of essentially-dead entries) we lift the floor more,
    #     when it sits just under threshold we lift less.
    max_raw = max(odds.values()) if odds else 0.0
    below_log_mean = sum(math.log(max(v, 1e-15)) for _, v in below) / len(below)
    below_geom_mean = math.exp(below_log_mean)
    tail_ratio = min(1.0, below_geom_mean / threshold)
    pad_factor = 1.1 + 0.7 * max_raw + 0.3 * (1.0 - tail_ratio)
    pad_factor = max(1.1, min(1.8, pad_factor))

    log_floor_target = math.log(floor * pad_factor)
    log_threshold = math.log(threshold)
    if len(below) == 1:
        spread = {below[0][0]: math.sqrt(floor * pad_factor * threshold)}
    else:
        # Map each below-threshold song's log(raw) linearly onto
        # [log(floor*1.1), log(threshold)] so within-tail log-distances
        # are preserved (just compressed into the visible band).
        b_min = max(below[0][1], 1e-15)
        b_max = max(below[-1][1], b_min * 1.0001)
        log_b_min = math.log(b_min)
        log_b_max = math.log(b_max)
        log_range = log_b_max - log_b_min
        target_range = log_threshold - log_floor_target

        spread = {}
        for sid, v in below:
            v_clamped = max(v, b_min)
            t = (math.log(v_clamped) - log_b_min) / log_range
            spread[sid] = math.exp(log_floor_target + t * target_range)

    above_sum_orig = sum(above.values())
    spread_sum = sum(spread.values())
    above_sum_target = max(0.0, 1.0 - spread_sum)

    if above_sum_orig <= 0:
        # No songs above threshold — normalize the spread to sum to 1.
        if spread_sum <= 0:
            return {sid: 1.0 / n for sid in odds}
        return {sid: v / spread_sum for sid, v in spread.items()}

    above_scale = above_sum_target / above_sum_orig
    result = {sid: v * above_scale for sid, v in above.items()}
    result.update(spread)
    return result


def _compute_winning_odds(
    songs: list,
    pred_by_set: dict,
    n_predictors: int,
) -> dict[int, float]:
    """
    Compute winning probability for each song in a final.

    Blends two complementary signals so the odds track predictor
    consensus directly without being washed out by the size of the field
    (a plain Plackett–Luce normalization shrinks the rank-1 share roughly
    like 1 / sum_r exp(-k(r-1)), which over 25+ songs makes even a clear
    favourite look weak):

    1. ``top1`` — fraction of predictors who put the song at rank 1.
       Maps consensus on the favourite directly to a win probability,
       independent of how many also-rans are in the field.
    2. ``pl`` — Plackett–Luce per-predictor softmax. Differentiates a
       song that's always 2nd/3rd from one nobody considers competitive,
       and gives some residual mass to the long tail.

    Both signals are proper distributions (sum to 1 across songs), so
    the alpha-blend is too. Tuning:
    - ``alpha`` controls how strongly consensus #1 picks dominate.
      0.7 means a unanimous favourite reaches ≈0.7 from top1 alone, with
      the PL term adding the remainder.
    - ``k`` controls the PL decay; moderate (0.4) so 2nd/3rd finishes
      still earn meaningful weight without flattening the tail to noise.
    """
    n_songs = len(songs)
    if n_predictors == 0 or n_songs == 0:
        return {song.id: 0.0 for song in songs}

    alpha = 0.7
    k = 0.4

    top1: dict[int, int] = {song.id: 0 for song in songs}
    pl_acc: dict[int, float] = {song.id: 0.0 for song in songs}

    for set_preds in pred_by_set.values():
        # Rank-1 tally
        for song in songs:
            if set_preds.get(song.id) == 1:
                top1[song.id] += 1

        # Plackett–Luce per-predictor softmax
        scores: dict[int, float] = {}
        for song in songs:
            rank = set_preds.get(song.id, n_songs)
            scores[song.id] = math.exp(-k * (rank - 1))
        total = sum(scores.values())
        if total <= 0:
            continue
        for sid, score in scores.items():
            pl_acc[sid] += score / total

    raw = {
        song.id: (
            alpha * (top1[song.id] / n_predictors)
            + (1 - alpha) * (pl_acc[song.id] / n_predictors)
        )
        for song in songs
    }

    # Keep every song's odds above 1/1000 — the bare PL tail otherwise
    # produces ugly numbers like 1/11000 for the dead-last entry — but
    # spread the low end log-uniformly into a small band [floor, 2×floor]
    # so the bottom rows show distinct values rather than a long row of
    # "0.10%". Only songs already below 0.2% get touched, so ranks above
    # the tail are essentially unchanged.
    return _smooth_low_odds(raw, threshold=0.002, floor=0.001)


def _compute_prediction_scores(
    real_positions: dict[int, int],
    songs: list,
    predictors: dict[str, dict[int, int]],
) -> tuple[dict[str, int], dict[str, list[dict]], dict[str, dict[int, int]]]:
    """Score each predictor by sum of (real_pos - predicted_pos)^2 across songs.

    Returns (scores, breakdown, penalty_by_song):
      - scores[username] = total score
      - breakdown[username] = per-song rows ordered by penalty descending
      - penalty_by_song[username][song_id] = penalty for that song
    """
    songs_by_id = {song.id: song for song in songs}

    scores: dict[str, int] = {}
    breakdown: dict[str, list[dict]] = {}
    penalty_by_song: dict[str, dict[int, int]] = {}
    for username, preds in predictors.items():
        total = 0
        rows: list[dict] = []
        per_song: dict[int, int] = {}
        for sid, predicted in preds.items():
            real = real_positions.get(sid)
            song = songs_by_id.get(sid)
            if real is None or song is None:
                continue
            penalty = (real - predicted) ** 2
            total += penalty
            per_song[sid] = penalty
            rows.append({
                "song": song,
                "predicted": predicted,
                "real": real,
                "penalty": penalty,
            })
        rows.sort(key=lambda r: r["penalty"], reverse=True)
        scores[username] = total
        breakdown[username] = rows
        penalty_by_song[username] = per_song
    return scores, breakdown, penalty_by_song


@bp.get("/<int:year>/<show>/predictions")
@with_permissions
def show_predictions(year: int, show: str, permissions: UserPermissions):
    _year = year
    show_data = get_show_id(show, _year)

    if not show_data:
        return render_template("error.html", error="Show not found"), 404

    if show_data.status != "full" and not permissions.can_view_restricted:
        return render_template(
            "error.html", error="You aren't allowed to access the predictions yet"
        ), 400

    if (
        show_data.voting_closes
        and show_data.voting_closes > dt_now()
        and not permissions.can_view_restricted
    ):
        return render_template("error.html", error="Voting hasn't closed yet."), 400

    # select_votes=True populates song.vote_data, which carries the running order
    songs = get_show_songs(_year, show, select_votes=True)
    if not songs:
        return render_template("error.html", error="No songs found for this show."), 404

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        """
        SELECT prediction_set.id, account.username, prediction_set.created_at
        FROM prediction_set
        JOIN account ON prediction_set.user_id = account.id
        WHERE prediction_set.show_id = %s
        ORDER BY prediction_set.created_at
    """,
        (show_data.id,),
    )
    pred_sets = cursor.fetchall()

    cursor.execute(
        """
        SELECT prediction.set_id, prediction.song_id, prediction.position
        FROM prediction
        JOIN prediction_set ON prediction.set_id = prediction_set.id
        WHERE prediction_set.show_id = %s
    """,
        (show_data.id,),
    )

    pred_by_set: dict[int, dict[int, int]] = defaultdict(dict)
    for row in cursor.fetchall():
        pred_by_set[row["set_id"]][row["song_id"]] = row["position"]

    n_predictors = len(pred_sets)
    n_qualifiers = (show_data.dtf or 0) + (show_data.sc or 0) + (show_data.special or 0)

    # Finals (no qualifier cutoff) get a winning-probability distribution;
    # semifinals get an independent per-song qualification probability.
    is_final = n_qualifiers <= 0
    if is_final:
        odds = _compute_winning_odds(songs, pred_by_set, n_predictors)
    else:
        odds = _compute_qualification_odds(songs, pred_by_set, n_predictors, n_qualifiers)

    # Build predictor dict ordered by submission time: {username: {song_id: position}}
    predictors: dict[str, dict] = {}
    for ps in pred_sets:
        predictors[ps["username"]] = pred_by_set.get(ps["id"], {})

    # Weighted prediction points: each predictor awards 12 * 0.827^(pos-1) points.
    # Songs are then ranked by total points to produce a predicted finishing order
    # that is independent of the qualification/winning odds.
    pred_points: dict[int, float] = {song.id: 0.0 for song in songs}
    for set_preds in pred_by_set.values():
        for sid, pos in set_preds.items():
            if sid in pred_points:
                pred_points[sid] += 12 * (0.827 ** (pos - 1))

    pred_rank: dict[int, int] = {}
    for rank, song in enumerate(
        sorted(songs, key=lambda s: pred_points[s.id], reverse=True), start=1
    ):
        pred_rank[song.id] = rank

    real_positions: dict[int, int] = {}
    if show_data.status in ("partial", "full"):
        cursor.execute(
            "SELECT song_id, place FROM country_show_results WHERE show_id = %s",
            (show_data.id,),
        )
        real_positions = {row["song_id"]: row["place"] for row in cursor.fetchall()}

    # Sort by real finishing place when results are public; otherwise by qualifying odds.
    if real_positions:
        songs.sort(key=lambda s: (real_positions.get(s.id) is None, real_positions.get(s.id, 0)))
    else:
        songs.sort(key=lambda s: odds[s.id], reverse=True)

    # Assign predicted-position colour classes (used as a left strip on each row).
    predicted_class: dict[int, str] = {}
    n_total = len(songs)
    if n_total:
        if is_final:
            predicted_class[songs[0].id] = "first"
            if n_total >= 2:
                predicted_class[songs[1].id] = "second"
            if n_total >= 3:
                predicted_class[songs[2].id] = "third"
            predicted_class[songs[-1].id] = "last"
        else:
            dtf_n = show_data.dtf or 0
            sc_n = show_data.sc or 0
            for i, song in enumerate(songs):
                if i < dtf_n:
                    predicted_class[song.id] = "direct-to-final"
                elif i < dtf_n + sc_n:
                    predicted_class[song.id] = "second-chance"
            predicted_class[songs[-1].id] = "last"

    # Pre-render copyable odds text
    copy_lines: list[str] = []
    for i, song in enumerate(songs, 1):
        prob = odds[song.id]
        decimal_odds = (1 / prob) if prob > 0 else float("inf")
        pct = prob * 100
        copy_lines.append(f"{i}. {song.country.name}: {decimal_odds:.2f} ({pct:.2f}%)")
    copy_text = "\n".join(copy_lines)

    # Copy box is an admin tool — hide it when the page is publicly visible
    show_copy = show_data.status != "full"

    predictor_scores: dict[str, int] = {}
    predictor_breakdown: dict[str, list[dict]] = {}
    predictor_penalty: dict[str, dict[int, int]] = {}
    if real_positions:
        predictor_scores, predictor_breakdown, predictor_penalty = (
            _compute_prediction_scores(real_positions, songs, predictors)
        )

    return render_template(
        "year/predictions.html",
        songs=songs,
        predictors=predictors,
        odds=odds,
        predicted_class=predicted_class,
        pred_points=pred_points,
        pred_rank=pred_rank,
        n_predictors=n_predictors,
        n_qualifiers=n_qualifiers,
        copy_text=copy_text,
        show_copy=show_copy,
        show=show,
        show_name=show_data.name,
        year=year,
        other_shows=get_other_shows(_year, show),
        predictor_scores=predictor_scores,
        predictor_breakdown=predictor_breakdown,
        predictor_penalty=predictor_penalty,
        real_positions=real_positions,
    )
