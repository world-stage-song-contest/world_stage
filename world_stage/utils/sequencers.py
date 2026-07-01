from abc import ABC, abstractmethod
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .songs import Song

type Bucket = deque[tuple[str, dict[int, int]]]


class LCG:
    def __init__(self, seed: int, a: int = 0x19660D, c: int = 0x3C6EF35F, m: int = 2**32) -> None:
        self.state = seed
        self.a = a
        self.c = c
        self.m = m
        self.seed = seed

    def next(self, limit: int | None) -> int:
        self.state = (self.state * self.a + self.c) % self.m
        if limit:
            return self.state % limit
        else:
            return self.state

    def shuffle(self, arr: list):
        n = len(arr)
        for i in range(n - 1, 0, -1):
            j = self.next(i + 1)
            arr[i], arr[j] = arr[j], arr[i]

    def sample[T](self, arr: list[T], k: int) -> list[T]:
        indices = list(range(len(arr)))
        self.shuffle(indices)
        return [arr[i] for i in indices[:k]]

    def lightly_shuffle[T](self, arr: list[T], num_swaps: int):
        n = len(arr)
        indices = self.sample(list(range(n - 1)), 2 * num_swaps)
        for i in range(0, len(indices), 2):
            a, b = indices[i], indices[i + 1]
            arr[a], arr[b] = arr[b], arr[a]


class AbstractVoteSequencer(ABC):
    def __init__(
        self,
        vote_dict: dict[str, dict[int, int]],
        vote_items: list["Song"],
        points: list[int],
        *,
        winner_weight: int = 2,
        swaps: int = 5,
        seed: int = 1,
    ) -> None:
        self.vote_dict = vote_dict
        self.vote_items = vote_items
        self.points = sorted(points, reverse=True)
        self.winner_weight = winner_weight
        self.num_swaps = swaps
        self.seed = seed
        self.first_half = len(vote_dict) // 3 * 2

        self.song_ids = [song.id for song in vote_items]
        self.submitter_by_song = {song.id: song.submitter for song in vote_items}
        self.song_by_submitter = {song.submitter: song.id for song in vote_items}

        self.high_threshold, self.medium_threshold = self._calculate_thresholds()
        self.final_scores = self._calculate_final_scores()
        self.known_winner = max(self.final_scores, key=self.final_scores.__getitem__)
        self.top_entries = self._get_top_entries(n=3)

    def _calculate_thresholds(self) -> tuple[int, int]:
        n = len(self.points)
        top = self.points[: n // 3]
        middle = self.points[n // 3 : 2 * n // 3]
        high_threshold = min(top) if top else 0
        medium_threshold = min(middle) if middle else 0
        return high_threshold, medium_threshold

    def _calculate_final_scores(self) -> dict[int, int]:
        scores: dict[int, int] = {(item.id): 0 for item in self.vote_items}
        for vote in self.vote_dict.values():
            for pts, item in vote.items():
                scores[item] += pts
        return scores

    def _get_top_entries(self, n: int) -> list[int]:
        sorted_items = sorted(self.final_scores.items(), key=lambda x: x[1], reverse=True)
        return [item for item, _ in sorted_items[:n]]

    def _classify_votes(self) -> tuple[Bucket, Bucket, Bucket, list[str]]:
        high: Bucket = deque()
        medium: Bucket = deque()
        low: Bucket = deque()
        early_voters: list[str] = []

        top_submitters = {
            self.submitter_by_song[song_id]
            for song_id in self.top_entries
            if song_id in self.submitter_by_song
        }

        for user, vote in self.vote_dict.items():
            if user in top_submitters:
                early_voters.append(user)
                continue

            winner_points = sum(pts for pts, item in vote.items() if item == self.known_winner)
            if winner_points >= self.high_threshold:
                high.append((user, vote))
            elif winner_points >= self.medium_threshold:
                medium.append((user, vote))
            else:
                low.append((user, vote))

        return low, medium, high, early_voters

    def _suspense_metric(self, temp_scores: dict[int, int], vote: dict[int, int]) -> int:
        sorted_scores = sorted(temp_scores.values(), reverse=True)
        gap = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else 0
        winner_points = sum(pts for pts, item in vote.items() if item == self.known_winner)
        return (winner_points * self.winner_weight) + gap

    @abstractmethod
    def get_order(self) -> list[str]:
        pass


class SuspensefulVoteSequencer(AbstractVoteSequencer):
    def get_order(self) -> list[str]:
        low, medium, high, early_voters = self._classify_votes()
        current_scores: dict[int, int] = {song_id: 0 for song_id in self.song_ids}
        final_order: list[str] = []
        lcg = LCG(self.seed)

        buckets: list[Bucket] = [low, medium, high]
        bucket_idx = 0
        remaining_voters = set(self.vote_dict.keys()) - set(final_order)

        while any(buckets):
            tried = 0
            while tried < len(buckets):
                bucket = buckets[bucket_idx % len(buckets)]
                bucket_idx += 1
                tried += 1

                if not bucket:
                    continue

                best_user: str = ""
                best_vote: dict[int, int] = {}
                best_score: float = float("inf")

                for user, vote in bucket:
                    if user not in remaining_voters:
                        continue
                    temp_scores = current_scores.copy()
                    for pts, item in vote.items():
                        temp_scores[item] += pts
                    score = self._suspense_metric(temp_scores, vote)
                    if score < best_score:
                        best_user, best_vote = user, vote
                        best_score = score

                for pts, item in best_vote.items():
                    current_scores[item] += pts
                final_order.append(best_user)
                remaining_voters.remove(best_user)

                # Remove from bucket
                bucket = deque((u, v) for u, v in bucket if u != best_user)
                buckets[(bucket_idx - 1) % len(buckets)] = bucket
                break

        for v in early_voters:
            num = lcg.next(self.first_half)
            final_order.insert(num, v)

        return final_order


class RandomVoteSequencer(AbstractVoteSequencer):
    def get_order(self) -> list[str]:
        _, _, _, early_voters = self._classify_votes()
        final_order_set = set(self.vote_dict.keys())

        for v in early_voters:
            final_order_set.remove(v)

        final_order = list(final_order_set)

        lcg = LCG(self.seed)

        lcg.shuffle(final_order)

        for v in early_voters:
            num = lcg.next(self.first_half)
            final_order.insert(num, v)

        return final_order


class ChronologicalVoteSequencer(AbstractVoteSequencer):
    def get_order(self) -> list[str]:
        return list(self.vote_dict.keys())
