from dataclasses import dataclass

from ..db import get_db


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
    votes: dict[int, int], rules: dict[int, BallotEntryRule]
) -> list[tuple[str, str | None, int, int | None]]:
    """Return structured policy violations for a complete score-to-song ballot."""
    errors: list[tuple[str, str | None, int, int | None]] = []
    score_by_song = {song_id: score for score, song_id in votes.items()}

    for score, song_id in votes.items():
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
