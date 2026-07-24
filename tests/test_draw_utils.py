from collections import Counter

import pytest

from world_stage.utils.draw import draw_semifinals

SHOWS = ["sf1", "sf2", "sf3", "sf4"]


@pytest.fixture
def _clean_songs():
    """Pure draw tests do not need the suite's database cleanup fixture."""
    yield


def _make_pots(sizes: list[int]) -> dict[int, list[dict]]:
    return {
        pot: [
            {
                "song_id": pot * 1_000 + index,
                "cc": f"C{pot}-{index}",
                "submitter": pot * 1_000 + index,
                "source_pot": pot,
            }
            for index in range(size)
        ]
        for pot, size in enumerate(sizes, start=1)
    }


def _draw(pots: dict[int, list[dict]], limits: list[int], seed: int = 2026):
    return draw_semifinals(pots, SHOWS, limits, seed)


def _assert_complete_balanced_draw(
    assignments: dict[str, list[dict]],
    pots: dict[int, list[dict]],
    limits: list[int],
):
    assert [len(assignments[show]) for show in SHOWS] == limits

    expected_ids = {entry["song_id"] for entries in pots.values() for entry in entries}
    drawn_ids = [entry["song_id"] for entries in assignments.values() for entry in entries]
    assert len(drawn_ids) == len(expected_ids)
    assert set(drawn_ids) == expected_ids

    for pot, entries in pots.items():
        counts = [sum(entry["source_pot"] == pot for entry in assignments[show]) for show in SHOWS]
        assert max(counts) - min(counts) <= 1
        assert min(counts) == len(entries) // len(SHOWS)
        assert max(counts) == (len(entries) + len(SHOWS) - 1) // len(SHOWS)


def test_many_pots_of_four_put_one_entry_from_each_pot_in_each_show():
    pots = _make_pots([4] * 12)

    assignments = _draw(pots, [12, 12, 12, 12])

    _assert_complete_balanced_draw(assignments, pots, [12, 12, 12, 12])
    for show in SHOWS:
        assert Counter(entry["source_pot"] for entry in assignments[show]) == Counter(
            {pot: 1 for pot in pots}
        )


def test_pots_of_four_and_eight_draw_one_or_two_complete_rounds():
    pots = _make_pots([4, 8, 4, 8, 8])

    assignments = _draw(pots, [8, 8, 8, 8])

    _assert_complete_balanced_draw(assignments, pots, [8, 8, 8, 8])
    for show in SHOWS:
        assert Counter(entry["source_pot"] for entry in assignments[show]) == Counter(
            {1: 1, 2: 2, 3: 1, 4: 2, 5: 2}
        )


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 2026])
def test_pots_from_four_through_eight_balance_all_leftovers(seed: int):
    pots = _make_pots([4, 5, 6, 7, 8])

    assignments = _draw(pots, [8, 8, 7, 7], seed)

    _assert_complete_balanced_draw(assignments, pots, [8, 8, 7, 7])

    extras_by_pot = {
        pot: {
            show
            for show in SHOWS
            if sum(entry["source_pot"] == pot for entry in assignments[show])
            > len(pots[pot]) // len(SHOWS)
        }
        for pot in (2, 3, 4)
    }
    assert len(extras_by_pot[2]) == 1
    assert len(extras_by_pot[3]) == 2
    assert len(extras_by_pot[4]) == 3
    # Pot 3's leftovers go to shows not lengthened by pot 2.
    assert extras_by_pot[2].isdisjoint(extras_by_pot[3])
    # Pot 4 must include the only show still shorter after pots 2 and 3.
    shortest_show = (set(SHOWS) - extras_by_pot[2] - extras_by_pot[3]).pop()
    assert shortest_show in extras_by_pot[4]


def test_draw_is_deterministic_for_the_same_seed():
    pots = _make_pots([4, 5, 6, 7, 8])

    first = _draw(pots, [8, 8, 7, 7], seed=12345)
    second = _draw(pots, [8, 8, 7, 7], seed=12345)

    assert first == second


def test_small_initial_pot_skips_complete_rounds_and_joins_leftover_allocation():
    pots = _make_pots([4, 3])

    assignments = _draw(pots, [2, 2, 2, 1])

    _assert_complete_balanced_draw(assignments, pots, [2, 2, 2, 1])
    shows_with_small_pot_entries = {
        show for show in SHOWS if any(entry["source_pot"] == 2 for entry in assignments[show])
    }
    assert len(shows_with_small_pot_entries) == 3


def test_entries_from_one_submitter_are_put_in_different_shows():
    pots = _make_pots([4, 4, 4, 4])
    shared_submitter = 999_999
    for entries in pots.values():
        entries[0]["submitter"] = shared_submitter

    assignments = _draw(pots, [4, 4, 4, 4])

    _assert_complete_balanced_draw(assignments, pots, [4, 4, 4, 4])
    for show in SHOWS:
        assert sum(entry["submitter"] == shared_submitter for entry in assignments[show]) == 1


def test_errors_when_a_submitter_has_more_entries_than_shows():
    pots = _make_pots([4, 4, 4, 4, 4])
    for entries in pots.values():
        entries[0]["submitter"] = 999_999

    with pytest.raises(ValueError, match="Cannot allocate pots without semifinal conflicts"):
        _draw(pots, [5, 5, 5, 5])


def test_entries_from_one_country_are_put_in_different_shows():
    pots = _make_pots([4, 4, 4, 4])
    for entries in pots.values():
        entries[0]["cc"] = "DUP"

    assignments = _draw(pots, [4, 4, 4, 4])

    for show in SHOWS:
        assert sum(entry["cc"] == "DUP" for entry in assignments[show]) == 1


def test_genre_and_language_are_balanced_across_shows():
    pots = _make_pots([4, 4, 4, 4])
    for entries in pots.values():
        for index, entry in enumerate(entries):
            entry["genre"] = "rock" if index < 2 else "pop"
            entry["language"] = "en" if index < 2 else "fr"

    assignments = _draw(pots, [4, 4, 4, 4])

    for show in SHOWS:
        assert Counter(entry["genre"] for entry in assignments[show]) == Counter(
            {"rock": 2, "pop": 2}
        )
        assert Counter(entry["language"] for entry in assignments[show]) == Counter(
            {"en": 2, "fr": 2}
        )


def test_errors_when_show_limits_cannot_hold_every_entry():
    pots = _make_pots([4, 4])

    with pytest.raises(ValueError, match="Cannot allocate pots without semifinal conflicts"):
        _draw(pots, [2, 2, 2, 1])
