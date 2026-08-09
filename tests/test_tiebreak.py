from collections import defaultdict

from world_stage.utils import VoteData


def vote_data(*, running_order, total_points, point_counts):
    data = VoteData(
        ro=running_order,
        total_votes=sum(point_counts.values()),
        max_pts=12,
        show_voters=10,
    )
    data.sum = total_points
    data.count = sum(point_counts.values())
    data.pts = defaultdict(int, point_counts)
    return data


def test_more_voting_jurors_wins_tiebreak():
    two_jurors = vote_data(
        running_order=2, total_points=24, point_counts={12: 2}
    )
    three_jurors = vote_data(
        running_order=3, total_points=24, point_counts={8: 3}
    )

    assert three_jurors > two_jurors


def test_highest_point_count_wins_countback():
    twelve_and_eight = vote_data(
        running_order=2, total_points=20, point_counts={12: 1, 8: 1}
    )
    two_tens = vote_data(
        running_order=1, total_points=20, point_counts={10: 2}
    )

    assert twelve_and_eight > two_tens


def test_earlier_running_order_wins_unresolved_tie():
    earlier = vote_data(
        running_order=2, total_points=20, point_counts={12: 1, 8: 1}
    )
    later = vote_data(
        running_order=7, total_points=20, point_counts={12: 1, 8: 1}
    )

    assert earlier > later
