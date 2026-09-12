import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import ceil
from typing import Any

BALANCE_KEYS = ("genre", "language", "subgenre")


@dataclass
class SearchBudget:
    remaining: int

    def visit(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise ValueError("Draw search limit reached. Check the pots and semifinal constraints.")


@dataclass
class DrawEntry:
    data: dict[str, Any]
    pot: int

    @property
    def song_id(self) -> int:
        return self.data["song_id"]

    @property
    def code(self) -> str:
        return self.data["cc"]

    @property
    def submitter(self) -> int:
        return self.data["submitter"]

    def tag(self, key: str) -> Any:
        return self.data.get(key)

    def tags(self, key: str) -> frozenset:
        value = self.tag(key)
        if key == "genre" and isinstance(value, list):
            return frozenset(tag for tag in value if tag)
        return frozenset([value]) if value else frozenset()

    def permits(self, show_number: int) -> bool:
        constraints = self.data.get("semifinal_constraints") or []
        included = {number for number in constraints if number > 0}
        excluded = {-number for number in constraints if number < 0}
        return (not included or show_number in included) and show_number not in excluded


@dataclass
class ShowState:
    name: str
    number: int
    limit: int
    entries: list[DrawEntry] = field(default_factory=list)
    submitters: set[int] = field(default_factory=set)
    pots: set[int] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)
    balance_counts: dict[str, Counter] = field(
        default_factory=lambda: {key: Counter() for key in BALANCE_KEYS}
    )


def _ceiling_by_key(entries: list[DrawEntry], key: str, n_shows: int) -> dict[Any, int]:
    counts = Counter(tag for entry in entries for tag in entry.tags(key))
    return {tag: ceil(count / n_shows) for tag, count in counts.items()}


def _can_place_regular(
    show: ShowState,
    entry: DrawEntry,
    balance_ceils: dict[str, dict] | None,
    *,
    check_pot: bool = True,
):
    if len(show.entries) >= show.limit:
        return False
    if not entry.permits(show.number):
        return False
    if entry.submitter in show.submitters:
        return False
    if check_pot and entry.pot in show.pots:
        return False
    if entry.code in show.codes:
        return False
    if balance_ceils:
        for key in BALANCE_KEYS:
            for tag in entry.tags(key):
                if show.balance_counts[key][tag] >= balance_ceils.get(key, {}).get(tag, 10**9):
                    return False
    return True


def _place(show: ShowState, entry: DrawEntry, *, track_pot: bool = True):
    show.entries.append(entry)
    show.submitters.add(entry.submitter)
    if track_pot:
        show.pots.add(entry.pot)
    show.codes.add(entry.code)
    for key in BALANCE_KEYS:
        for tag in entry.tags(key):
            show.balance_counts[key][tag] += 1


def _remove(show: ShowState, entry: DrawEntry, *, track_pot: bool = True):
    show.entries.pop()
    show.submitters.remove(entry.submitter)
    if track_pot:
        show.pots.remove(entry.pot)
    show.codes.remove(entry.code)
    for key in BALANCE_KEYS:
        for tag in entry.tags(key):
            show.balance_counts[key][tag] -= 1
            if show.balance_counts[key][tag] <= 0:
                del show.balance_counts[key][tag]


def _regular_options(
    pot: list[DrawEntry],
    shows: list[ShowState],
    rng: random.Random,
    balance_ceils: dict[str, dict] | None,
    *,
    check_pot: bool = True,
    budget: SearchBudget | None = None,
) -> list[list[tuple[ShowState, DrawEntry]]]:
    entries = pot[:]
    rng.shuffle(entries)
    options: list[list[tuple[ShowState, DrawEntry]]] = []

    def visit(index: int, used_shows: set[str], placements: list[tuple[ShowState, DrawEntry]]):
        if budget is not None:
            budget.visit()
        if index == len(entries):
            options.append(placements[:])
            return

        entry = entries[index]
        candidates = [
            show
            for show in shows
            if show.name not in used_shows
            and _can_place_regular(show, entry, balance_ceils, check_pot=check_pot)
        ]
        rng.shuffle(candidates)
        candidates.sort(key=lambda show: show.limit - len(show.entries), reverse=True)
        for show in candidates:
            used_shows.add(show.name)
            placements.append((show, entry))
            visit(index + 1, used_shows, placements)
            placements.pop()
            used_shows.remove(show.name)

    visit(0, set(), [])
    return options


def _pot_rounds(
    pots: list[list[DrawEntry]], n_shows: int, rng: random.Random
) -> tuple[list[list[DrawEntry]], list[list[DrawEntry]]]:
    """Split pots into complete rounds and final, incomplete rounds.

    Complete rounds are interleaved by round number: every pot supplies one
    entry to every show before an oversized pot supplies its next complete
    round.  Leftovers retain pot order for the balancing pass.
    """
    shuffled_pots = [pot[:] for pot in pots]
    for pot in shuffled_pots:
        rng.shuffle(pot)
    complete: list[list[DrawEntry]] = []
    max_rounds = max((len(pot) // n_shows for pot in shuffled_pots), default=0)
    for round_index in range(max_rounds):
        start = round_index * n_shows
        for pot in shuffled_pots:
            if len(pot) >= start + n_shows:
                complete.append(pot[start : start + n_shows])

    leftovers = [
        pot[(len(pot) // n_shows) * n_shows :] for pot in shuffled_pots if len(pot) % n_shows
    ]
    return complete, leftovers


def _assign_pot_rounds(
    pots: list[list[DrawEntry]],
    shows: list[ShowState],
    rng: random.Random,
    balance_ceils: dict[str, dict] | None,
):
    budget = SearchBudget(10_000 if balance_ceils else 100_000)
    complete, leftovers = _pot_rounds(pots, len(shows), rng)
    # The first leftover pot is distributed normally. Subsequent leftover
    # pots prefer shows which currently contain fewer entries.
    rounds = [(entries, False) for entries in complete]
    rounds.extend((entries, index > 0) for index, entries in enumerate(leftovers))

    def option_score(option: list[tuple[ShowState, DrawEntry]], prioritize_short: bool) -> tuple:
        balance = sum(_balance_cost(show, entry) for show, entry in option)
        if not prioritize_short:
            return (balance,)
        sizes = sorted(len(show.entries) for show, _entry in option)
        return (sum(sizes), sizes, balance)

    def visit(index: int) -> bool:
        if index == len(rounds):
            return True

        entries, prioritize_short = rounds[index]
        budget.visit()
        options = _regular_options(
            entries, shows, rng, balance_ceils, check_pot=False, budget=budget
        )
        rng.shuffle(options)
        options.sort(key=lambda option: option_score(option, prioritize_short))
        for option in options:
            for show, entry in option:
                _place(show, entry, track_pot=False)
            if visit(index + 1):
                return True
            for show, entry in reversed(option):
                _remove(show, entry, track_pot=False)
        return False

    if not visit(0):
        raise ValueError("Cannot allocate pots without semifinal conflicts")


def _assign_regular(
    pots: list[list[DrawEntry]],
    shows: list[ShowState],
    rng: random.Random,
    balance_ceils: dict[str, dict] | None,
):
    remaining = [pot for pot in pots if pot]

    def visit(open_pots: list[list[DrawEntry]]) -> bool:
        if not open_pots:
            return True

        best_pot = None
        best_options = None
        for pot in open_pots:
            options = _regular_options(pot, shows, rng, balance_ceils)
            if not options:
                return False
            if best_options is None or len(options) < len(best_options):
                best_pot = pot
                best_options = options
                if len(options) == 1:
                    break

        assert best_pot is not None and best_options is not None
        rng.shuffle(best_options)
        next_pots = [pot for pot in open_pots if pot is not best_pot]
        for option in best_options:
            for show, entry in option:
                _place(show, entry)
            if visit(next_pots):
                return True
            for show, entry in reversed(option):
                _remove(show, entry)
        return False

    if not visit(remaining):
        raise ValueError("Cannot allocate pots without semifinal conflicts")


def _balance_cost(show: ShowState, entry: DrawEntry) -> int:
    return sum(
        (2 if key == "subgenre" else 1) * show.balance_counts[key][tag]
        for key in BALANCE_KEYS
        for tag in entry.tags(key)
    )


def _assign_single_pot(entries: list[DrawEntry], shows: list[ShowState], rng: random.Random):
    budget = SearchBudget(100_000)
    by_country: dict[str, list[DrawEntry]] = defaultdict(list)
    for entry in entries:
        by_country[entry.code].append(entry)

    ordered_entries = entries[:]
    rng.shuffle(ordered_entries)
    tag_counts = {
        key: Counter(tag for entry in entries for tag in entry.tags(key))
        for key in BALANCE_KEYS
    }
    ordered_entries.sort(
        key=lambda entry: (
            len(by_country[entry.code]),
            sum(
                (2 if key == "subgenre" else 1) * (tag_counts[key][tag] - 1)
                for key in BALANCE_KEYS
                for tag in entry.tags(key)
            ),
        ),
        reverse=True,
    )

    def score(show: ShowState, entry: DrawEntry):
        balance_count = _balance_cost(show, entry)
        return (
            Counter(e.code for e in show.entries)[entry.code] * 1_000_000
            + balance_count * 1_000
            - (show.limit - len(show.entries)) * 10
            + rng.random()
        )

    def candidates(entry: DrawEntry):
        result = [
            show
            for show in shows
            if len(show.entries) < show.limit
            and entry.submitter not in show.submitters
            and entry.permits(show.number)
        ]
        result.sort(key=lambda show: score(show, entry))
        return result

    def visit(unplaced: list[DrawEntry]) -> bool:
        budget.visit()
        if not unplaced:
            return True
        options = [(entry, candidates(entry)) for entry in unplaced]
        entry, available = min(options, key=lambda item: len(item[1]))
        for show in available:
            _place(show, entry, track_pot=False)
            if visit([candidate for candidate in unplaced if candidate is not entry]):
                return True
            _remove(show, entry, track_pot=False)
        return False

    if not visit(ordered_entries):
        raise ValueError("Cannot allocate entries without semifinal conflicts")


def _conflicts(a: DrawEntry | None, b: DrawEntry | None):
    if not a or not b:
        return False
    return (
        a.code == b.code
        or a.submitter == b.submitter
        or any(a.tags(key) & b.tags(key) for key in ("genre", "language"))
    )


def spread_running_order(entries: list[DrawEntry], rng: random.Random) -> list[DrawEntry]:
    if not entries:
        return []

    by_country: dict[str, list[DrawEntry]] = defaultdict(list)
    for entry in entries:
        by_country[entry.code].append(entry)
    for group in by_country.values():
        rng.shuffle(group)

    multi = [group for group in by_country.values() if len(group) > 1]
    single = [group[0] for group in by_country.values() if len(group) == 1]
    rng.shuffle(single)
    multi.sort(key=len, reverse=True)

    result: list[DrawEntry | None] = [None] * len(entries)
    for group in multi:
        stride = len(entries) / len(group)
        base = rng.randrange(max(1, int(stride)))
        for i, entry in enumerate(group):
            pos = int(base + i * stride) % len(entries)
            while result[pos] is not None:
                pos = (pos + 1) % len(entries)
            result[pos] = entry

    cursor = 0
    for entry in single:
        while result[cursor] is not None:
            cursor += 1
        result[cursor] = entry

    for _ in range(6):
        swapped = False
        for i in range(len(result) - 1):
            if not _conflicts(result[i], result[i + 1]):
                continue
            for j in range(i + 2, len(result)):
                if _conflicts(result[i], result[j]):
                    continue
                right_i = result[i + 2] if i + 2 < len(result) else None
                left_j = result[j - 1]
                right_j = result[j + 1] if j + 1 < len(result) else None
                if _conflicts(result[j], None if right_i is result[i + 1] else right_i):
                    continue
                if _conflicts(result[i + 1], None if left_j is result[i + 1] else left_j):
                    continue
                if _conflicts(result[i + 1], right_j):
                    continue
                result[i + 1], result[j] = result[j], result[i + 1]
                swapped = True
                break
        if not swapped:
            break

    ordered = [entry for entry in result if entry is not None]
    if any(entry.tags("genre") or entry.tags("subgenre") for entry in ordered):
        _spread_tags(ordered)
    return ordered


def _spread_tags(entries: list[DrawEntry]) -> None:
    """Weight spacing by shared tags. Give subgenre matches twice the weight."""
    def pair_cost(a: DrawEntry, b: DrawEntry, distance: int) -> tuple[float, ...]:
        return (
            float(distance == 1 and (a.code == b.code or a.submitter == b.submitter)),
            float(a.code == b.code) / distance,
            sum(
                (2 if key == "subgenre" else 1) * len(a.tags(key) & b.tags(key))
                for key in BALANCE_KEYS
            ) / distance,
        )

    for _ in range(6):
        improved = False
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                delta = [0.0, 0.0, 0.0]
                for k, entry in enumerate(entries):
                    if k in (i, j):
                        continue
                    before = pair_cost(entries[i], entry, abs(i - k))
                    other_before = pair_cost(entries[j], entry, abs(j - k))
                    after = pair_cost(entries[j], entry, abs(i - k))
                    other_after = pair_cost(entries[i], entry, abs(j - k))
                    for component in range(len(delta)):
                        delta[component] += (
                            after[component] + other_after[component]
                            - before[component] - other_before[component]
                        )
                if tuple(round(value, 10) for value in delta) < (0, 0, 0):
                    entries[i], entries[j] = entries[j], entries[i]
                    improved = True
        if not improved:
            break


def draw_semifinals(
    pots: dict[int, list[dict]],
    show_names: list[str],
    show_limits: list[int],
    seed: int,
    *,
    single_pot: bool = False,
) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    shows = [
        ShowState(name=name, number=number, limit=limit)
        for number, (name, limit) in enumerate(
            zip(show_names, show_limits, strict=True), start=1
        )
    ]
    draw_pots = [
        [DrawEntry(data=dict(entry), pot=pot) for entry in entries] for pot, entries in pots.items()
    ]

    if single_pot:
        _assign_single_pot([entry for pot in draw_pots for entry in pot], shows, rng)
    else:
        all_entries = [entry for pot in draw_pots for entry in pot]
        balance_ceils = {key: _ceiling_by_key(all_entries, key, len(shows)) for key in BALANCE_KEYS}
        for ceilings in (
            balance_ceils,
            {key: value for key, value in balance_ceils.items() if key != "subgenre"},
            None,
        ):
            try:
                _assign_pot_rounds(draw_pots, shows, rng, ceilings)
                break
            except ValueError:
                for show in shows:
                    show.entries.clear()
                    show.submitters.clear()
                    show.pots.clear()
                    show.codes.clear()
                    show.balance_counts = {key: Counter() for key in BALANCE_KEYS}
                if ceilings is None:
                    raise

    for show in shows:
        if len(show.entries) != show.limit:
            raise ValueError(
                f"Show {show.name} has {len(show.entries)} entries but needs {show.limit}"
            )

    return {
        show.name: [entry.data for entry in spread_running_order(show.entries, rng)]
        for show in shows
    }


def draw_running_order(entries: list[dict], seed: int | str) -> list[dict]:
    rng = random.Random(seed)
    draw_entries = [DrawEntry(data=dict(entry), pot=entry.get("pot") or 0) for entry in entries]
    rng.shuffle(draw_entries)
    return [entry.data for entry in spread_running_order(draw_entries, rng)]
