from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from world_stage.utils.draw import draw_running_order, draw_semifinals

SHOWS = ["sf1", "sf2", "sf3", "sf4"]


def _make_pots(sizes: list[int]) -> dict[int, list[dict]]:
    return {
        pot: [
            {
                "song_id": pot * 1_000 + index,
                "cc": f"C{pot}-{index}",
                "submitter": pot * 1_000 + index,
                "source_pot": pot,
                "genre": [index % 3 + 1],
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


@settings(max_examples=40, deadline=None)
@given(
    size=st.integers(min_value=3, max_value=15),
    tag=st.integers(min_value=1, max_value=32767),
    seed=st.integers(),
)
def test_running_order_spreads_a_subgenre_within_one_genre(size, tag, seed):
    entries = _make_pots([size])[1]
    for index, entry in enumerate(entries):
        entry.update(genre=[1], language=None, subgenre=tag if index < 2 else None)

    result = draw_running_order(entries, seed)

    positions = [index for index, entry in enumerate(result) if entry["subgenre"] == tag]
    assert positions == [0, size - 1]
    assert Counter(entry["song_id"] for entry in result) == Counter(
        entry["song_id"] for entry in entries
    )
    assert result == draw_running_order(entries, seed)


@settings(max_examples=30, deadline=None)
@given(
    tag=st.integers(min_value=1, max_value=32767),
    seed=st.integers(),
    constrained=st.booleans(),
    single_pot=st.booleans(),
)
def test_subgenres_are_balanced_unless_semifinal_constraints_prevent_it(
    tag, seed, constrained, single_pot
):
    pots = _make_pots([2, 2])
    for entries in pots.values():
        for index, entry in enumerate(entries):
            entry.update(genre=[1], language=None, subgenre=tag if index == 0 else None)
            if constrained and index == 0:
                entry["semifinal_constraints"] = [1]

    result = draw_semifinals(pots, SHOWS[:2], [2, 2], seed, single_pot=single_pot)

    counts = [sum(entry["subgenre"] == tag for entry in result[show]) for show in SHOWS[:2]]
    assert counts == ([2, 0] if constrained else [1, 1])


@settings(max_examples=40, deadline=None)
@given(
    tags=st.lists(st.integers(min_value=1, max_value=32767), min_size=3, max_size=8, unique=True),
    seed=st.integers(),
)
def test_running_order_separates_larger_genre_overlaps_more(tags, seed):
    entries = _make_pots([3])[1]
    for entry, genres in zip(entries, [tags, tags[1:], tags[:1]], strict=True):
        entry.update(genre=genres, language=None)

    result = draw_running_order(entries, seed)
    positions = {entry["song_id"]: index for index, entry in enumerate(result)}
    most_shared_distance = abs(positions[entries[0]["song_id"]] - positions[entries[1]["song_id"]])
    least_shared_distance = abs(positions[entries[0]["song_id"]] - positions[entries[2]["song_id"]])
    assert most_shared_distance > least_shared_distance


@settings(max_examples=30, deadline=None)
@given(
    tags=st.lists(st.integers(min_value=1, max_value=32767), min_size=3, max_size=6, unique=True),
    seed=st.integers(),
    single_pot=st.booleans(),
)
def test_semifinals_separate_overlapping_genre_arrays(tags, seed, single_pot):
    pots = _make_pots([2, 2])
    for entries in pots.values():
        for index, entry in enumerate(entries):
            entry.update(genre=tags[index:index + 2], language=None)
    pots[2][0]["genre"] = [tags[0]]
    pots[2][1]["genre"] = [tags[-1]]

    result = draw_semifinals(pots, SHOWS[:2], [2, 2], seed, single_pot=single_pot)
    for entries in result.values():
        assert not set(entries[0]["genre"]) & set(entries[1]["genre"])


@settings(max_examples=30, deadline=None)
@given(
    tags=st.lists(st.integers(min_value=1, max_value=32767), min_size=3, max_size=8, unique=True),
    seed=st.integers(),
    single_pot=st.booleans(),
)
def test_semifinals_prefer_separating_larger_overlaps_when_some_overlap_is_required(
    tags, seed, single_pot
):
    pots = _make_pots([2, 2])
    pots[1][0].update(genre=tags, language=None, semifinal_constraints=[1])
    pots[1][1].update(genre=[], language=None, semifinal_constraints=[2])
    pots[2][0].update(genre=tags[1:], language=None)
    pots[2][1].update(genre=tags[:1], language=None)

    result = draw_semifinals(pots, SHOWS[:2], [2, 2], seed, single_pot=single_pot)

    assert pots[2][0] in result["sf2"]
    assert pots[2][1] in result["sf1"]


@settings(max_examples=10, deadline=None)
@given(
    prefix_size=st.integers(min_value=10, max_value=14),
    seed=st.integers(),
    tags=st.lists(st.integers(min_value=1, max_value=32767), min_size=3, max_size=3, unique=True),
)
def test_impossible_genre_balance_still_produces_a_valid_draw(prefix_size, seed, tags):
    pots = _make_pots([2] * (prefix_size + 3))
    for entries in pots.values():
        for entry in entries:
            entry.update(genre=[], language=None)
    for index in range(3):
        pots[prefix_size + index + 1][0]["genre"] = [tags[index], tags[(index + 1) % 3]]

    result = draw_semifinals(pots, SHOWS[:2], [len(pots)] * 2, seed)

    assert [len(entries) for entries in result.values()] == [len(pots)] * 2
    assert Counter(entry["song_id"] for entries in result.values() for entry in entries) == Counter(
        entry["song_id"] for entries in pots.values() for entry in entries
    )
    for entries in result.values():
        assert len({entry["source_pot"] for entry in entries}) == len(pots)
