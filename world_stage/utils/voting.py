from dataclasses import dataclass

from ..db import fetchone, get_db


@dataclass(frozen=True)
class BallotEntryRule:
    kind: str
    reason: str | None
    required_score: int | None
    score_cap: int

    def permits(self, score: int) -> bool:
        if self.kind == "FORBIDDEN":
            return False
        if self.kind == "FORCED":
            return score == self.required_score
        return True


def get_ballot_entry_rules(
    show_id: int,
    result_mode: str,
    voter_id: int,
    country_id: str | None,
    song_ids: list[int],
) -> dict[int, BallotEntryRule]:
    """Load the authoritative rule for every requested ballot-entry pair."""
    if not song_ids:
        return {}

    cursor = get_db().cursor()
    cursor.execute(
        """
        SELECT requested.song_id, rule.rule_kind, rule.rule_reason,
               rule.required_score, rule.score_cap
        FROM unnest(%s::bigint[]) AS requested(song_id)
        CROSS JOIN LATERAL ballot_entry_rule(%s, %s, %s, %s, requested.song_id) rule
        """,
        (song_ids, show_id, result_mode, voter_id, country_id),
    )
    return {
        row["song_id"]: BallotEntryRule(
            kind=row["rule_kind"],
            reason=row["rule_reason"],
            required_score=row["required_score"],
            score_cap=row["score_cap"],
        )
        for row in cursor.fetchall()
    }


def ballot_rule_errors(
    votes: list[tuple[int, int]], rules: dict[int, BallotEntryRule]
) -> list[tuple[str, str | None, int, int | None]]:
    """Return structured policy violations for a complete ballot."""
    errors: list[tuple[str, str | None, int, int | None]] = []
    score_by_song = {song_id: score for score, song_id in votes}

    for score, song_id in votes:
        rule = rules.get(song_id)
        if rule is None:
            continue
        if rule.kind == "FORBIDDEN":
            errors.append(("forbidden", rule.reason, song_id, score))
        elif rule.kind == "FORCED" and score != rule.required_score:
            errors.append(("forced", rule.reason, song_id, rule.required_score))

    for song_id, rule in rules.items():
        if rule.kind != "FORCED":
            continue
        if score_by_song.get(song_id) != rule.required_score:
            violation = ("forced", rule.reason, song_id, rule.required_score)
            if violation not in errors:
                errors.append(violation)

    return errors


def get_point_system(point_system_id: int | None) -> dict | None:
    if point_system_id is None:
        return None
    cursor = get_db().cursor()
    cursor.execute(
        """SELECT id, kind, metadata
           FROM point_system WHERE id = %s""",
        (point_system_id,),
    )
    return dict(fetchone(cursor))


def pool_required_items(system: dict) -> int:
    count = system["min_items"]
    if system["require_all_points"]:
        cap = system.get("max_points_per_item") or system["total_points"]
        count = max(count, (system["total_points"] + cap - 1) // cap)
    return count


def pool_max_score(system: dict) -> int:
    available = system["total_points"] - max(pool_required_items(system) - 1, 0) * system.get(
        "min_points_per_item", 1
    )
    return min(system.get("max_points_per_item") or system["total_points"], available)


def validate_pool_settings(system: dict) -> str | None:
    for key in (
        "total_points", "min_items", "max_items", "min_points_per_item", "max_points_per_item"
    ):
        value = system.get(key, 1 if key == "min_points_per_item" else None)
        if key in ("max_items", "max_points_per_item") and value is None:
            continue
        if type(value) is not int or not (0 if key == "min_items" else 1) <= value <= 2147483647:
            return "Point pool settings must use positive integers. Minimum items may be zero."
    if type(system.get("require_all_points")) is not bool:
        return "Require all points must be a boolean."
    minimum = system.get("min_points_per_item", 1)
    cap = system.get("max_points_per_item") or system["total_points"]
    if minimum > min(cap, system["total_points"]):
        return "Minimum points per song must not exceed the maximum or the point pool."
    required = pool_required_items(system)
    if required * minimum > system["total_points"] or (
        system.get("max_items") is not None and required > system["max_items"]
    ):
        return "The point pool and song limits must allow a valid ballot."
    return None


def ballot_errors(votes: list[tuple[int, int]], show, song_ids) -> list[str]:
    scores = [score for score, _ in votes]
    chosen = [song_id for _, song_id in votes]
    errors = []
    if len(chosen) != len(set(chosen)):
        errors.append("A song can only receive one score.")
    if any(song_id not in song_ids for song_id in chosen):
        errors.append("Votes must reference songs in this show.")
    system = show.point_system
    if system["kind"] == "ranked":
        if sorted(scores) != sorted(show.points):
            errors.append("Votes must contain each show score exactly once.")
        return errors
    system = system["metadata"]
    minimum = system.get("min_points_per_item", 1)
    if any(score < minimum for score in scores):
        errors.append(f"Each score must be at least {minimum}.")
    cap = system.get("max_points_per_item")
    if cap is not None and any(score > cap for score in scores):
        errors.append(f"Each score must be at most {cap}.")
    if len(votes) < system["min_items"]:
        errors.append(f"Vote for at least {system['min_items']} songs.")
    maximum = system.get("max_items")
    if maximum is not None and len(votes) > maximum:
        errors.append(f"Vote for at most {maximum} songs.")
    if sum(scores) > system["total_points"]:
        errors.append(f"You can use at most {system['total_points']} points.")
    elif system["require_all_points"] and sum(scores) != system["total_points"]:
        errors.append(f"You must use all {system['total_points']} points.")
    return errors


def parse_ballot_form(form, show, song_ids) -> tuple[list[tuple[int, int]], list[str]]:
    votes = []
    errors = []
    pool = show.point_system["kind"] == "pool"
    keys = (
        [key for key in form if key.startswith("song-")]
        if pool
        else [f"pts-{point}" for point in show.points]
    )
    for key in keys:
        value = form.get(key)
        if pool and not value:
            continue
        try:
            if len(form.getlist(key)) != 1:
                raise ValueError
            score, song_id = (int(value), int(key[5:])) if pool else (int(key[4:]), int(value))
            if not pool or score != 0:
                votes.append((score, song_id))
        except (ValueError, TypeError):
            errors.append("Enter a whole number for each vote.")
    return votes, errors + ballot_errors(votes, show, song_ids)


def result_points(show) -> list[int]:
    if show.point_system["kind"] == "ranked":
        return sorted(show.points, reverse=True)
    cursor = get_db().cursor()
    cursor.execute(
        """SELECT DISTINCT vote.score FROM vote
           JOIN vote_set ON vote_set.id = vote.vote_set_id
           WHERE vote_set.show_id = %s AND vote.score > 0 ORDER BY vote.score DESC""",
        (show.id,),
    )
    return [row["score"] for row in cursor.fetchall()]
