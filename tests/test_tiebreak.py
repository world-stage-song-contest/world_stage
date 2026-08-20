from collections import defaultdict

from hypothesis import given
from hypothesis import strategies as st

from world_stage.utils import VoteData


@st.composite
def vote_data(draw):
    point_counts = draw(
        st.dictionaries(
            keys=st.integers(min_value=1, max_value=20),
            values=st.integers(min_value=1, max_value=20),
            max_size=8,
        )
    )
    penalty = draw(st.integers(min_value=0, max_value=30))
    data = VoteData(
        ro=draw(st.integers(min_value=1, max_value=100)),
        total_votes=sum(point_counts.values()),
        max_pts=max(point_counts, default=0),
        show_voters=draw(st.integers(min_value=0, max_value=100)),
    )
    data.sum = sum(points * count for points, count in point_counts.items()) - penalty
    data.count = sum(point_counts.values())
    data.pts = defaultdict(int, point_counts)
    return data


def _comparison_key(data: VoteData, all_point_values: set[int]) -> tuple:
    return (
        data.sum,
        data.count,
        *(data.pts.get(points, 0) for points in sorted(all_point_values, reverse=True)),
        -data.ro,
    )


@given(left=vote_data(), right=vote_data())
def test_tiebreak_matches_its_behavioral_priority_order(left, right):
    point_values = left.pts.keys() | right.pts.keys()
    left_key = _comparison_key(left, point_values)
    right_key = _comparison_key(right, point_values)

    assert (left < right) is (left_key < right_key)
    assert (left > right) is (left_key > right_key)
    assert (left == right) is (left_key == right_key)
