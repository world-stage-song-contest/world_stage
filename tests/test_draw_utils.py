from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.draw import draw_semifinals

SHOWS = ["sf1", "sf2", "sf3", "sf4"]


def _make_pots(sizes: list[int]) -> dict[int, list[dict]]:
    return {
        pot: [
            {
                "song_id": pot * 1_000 + index,
                "cc": f"C{pot}-{index}",
                "submitter": pot * 1_000 + index,
                "source_pot": pot,
                "genre": f"genre-{index % 3}",
                "language": f"language-{index % 2}",
            }
            for index in range(size)
        ]
        for pot, size in enumerate(sizes, start=1)
    }


def _balanced_limits(total: int) -> list[int]:
    minimum, larger_shows = divmod(total, len(SHOWS))
    return [minimum + (index < larger_shows) for index in range(len(SHOWS))]


def _flatten(assignments: dict[str, list[dict]]) -> list[dict]:
    return [entry for show in SHOWS for entry in assignments[show]]


@settings(max_examples=40, deadline=None)
@given(
    sizes=st.lists(st.integers(min_value=1, max_value=8), min_size=1, max_size=4),
    seed=st.integers(),
)
def test_draw_preserves_entries_capacity_and_balances_each_pot(sizes, seed):
    pots = _make_pots(sizes)
    limits = _balanced_limits(sum(sizes))

    assignments = draw_semifinals(pots, SHOWS, limits, seed)

    assert [len(assignments[show]) for show in SHOWS] == limits
    assert Counter(entry["song_id"] for entry in _flatten(assignments)) == Counter(
        entry["song_id"] for entries in pots.values() for entry in entries
    )
    for pot, entries in pots.items():
        counts = [sum(entry["source_pot"] == pot for entry in assignments[show]) for show in SHOWS]
        assert max(counts) - min(counts) <= 1
        assert sum(counts) == len(entries)

    assert assignments == draw_semifinals(pots, SHOWS, limits, seed)


@settings(max_examples=50, deadline=None)
@given(
    duplicate_count=st.integers(min_value=1, max_value=len(SHOWS)),
    seed=st.integers(),
    shared_field=st.sampled_from(["submitter", "cc"]),
)
def test_draw_separates_entries_that_share_a_submitter_or_country(
    duplicate_count, seed, shared_field
):
    pots = _make_pots([len(SHOWS)] * duplicate_count)
    for entries in pots.values():
        entries[0][shared_field] = "shared"

    assignments = draw_semifinals(
        pots,
        SHOWS,
        [duplicate_count] * len(SHOWS),
        seed,
    )

    shared_counts = [
        sum(entry[shared_field] == "shared" for entry in assignments[show]) for show in SHOWS
    ]
    assert max(shared_counts) <= 1
    assert sum(shared_counts) == duplicate_count


@settings(max_examples=50, deadline=None)
@given(
    data=st.data(),
    show_count=st.integers(min_value=2, max_value=len(SHOWS)),
    seed=st.integers(),
    constraint_kind=st.sampled_from(["include", "exclude"]),
)
def test_draw_obeys_semifinal_constraints(data, show_count, seed, constraint_kind):
    shows = SHOWS[:show_count]
    destinations = data.draw(st.permutations(range(1, show_count + 1)))
    pots = _make_pots([show_count])

    for entry, destination in zip(pots[1], destinations, strict=True):
        if constraint_kind == "include":
            entry["semifinal_constraints"] = [destination]
        else:
            entry["semifinal_constraints"] = [
                -number for number in range(1, show_count + 1) if number != destination
            ]

    assignments = draw_semifinals(pots, shows, [1] * show_count, seed)

    assigned_show = {
        entry["song_id"]: number
        for number, show in enumerate(shows, start=1)
        for entry in assignments[show]
    }
    assert assigned_show == {
        entry["song_id"]: destination
        for entry, destination in zip(pots[1], destinations, strict=True)
    }


@settings(max_examples=30, deadline=None)
@given(
    sizes=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=3),
    seed=st.integers(),
)
def test_draw_rejects_capacity_that_cannot_hold_every_entry(sizes, seed):
    pots = _make_pots(sizes)
    limits = _balanced_limits(sum(sizes))
    limits[-1] -= 1

    with pytest.raises(ValueError):
        draw_semifinals(pots, SHOWS, limits, seed)
