import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import ceil
from typing import Any

BALANCE_KEYS = ("genre", "language")


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


@dataclass
class ShowState:
    name: str
    limit: int
    entries: list[DrawEntry] = field(default_factory=list)
    submitters: set[int] = field(default_factory=set)
    pots: set[int] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)
    balance_counts: dict[str, Counter] = field(
        default_factory=lambda: {key: Counter() for key in BALANCE_KEYS}
    )


def _ceiling_by_key(entries: list[DrawEntry], key: str, n_shows: int) -> dict[Any, int]:
    counts = Counter(e.tag(key) for e in entries if e.tag(key))
    return {tag: ceil(count / n_shows) for tag, count in counts.items()}


def _can_place_regular(show: ShowState, entry: DrawEntry, balance_ceils: dict[str, dict] | None):
    if len(show.entries) >= show.limit:
        return False
    if entry.submitter in show.submitters:
        return False
    if entry.pot in show.pots:
        return False
    if entry.code in show.codes:
        return False
    if balance_ceils:
        for key in BALANCE_KEYS:
            tag = entry.tag(key)
            if tag and show.balance_counts[key][tag] >= balance_ceils[key].get(tag, 10**9):
                return False
    return True


def _place(show: ShowState, entry: DrawEntry, *, track_pot: bool = True):
    show.entries.append(entry)
    show.submitters.add(entry.submitter)
    if track_pot:
        show.pots.add(entry.pot)
    show.codes.add(entry.code)
    for key in BALANCE_KEYS:
        tag = entry.tag(key)
        if tag:
            show.balance_counts[key][tag] += 1


def _remove(show: ShowState, entry: DrawEntry, *, track_pot: bool = True):
    show.entries.pop()
    show.submitters.remove(entry.submitter)
    if track_pot:
        show.pots.remove(entry.pot)
    show.codes.remove(entry.code)
    for key in BALANCE_KEYS:
        tag = entry.tag(key)
        if not tag:
            continue
        show.balance_counts[key][tag] -= 1
        if show.balance_counts[key][tag] <= 0:
            del show.balance_counts[key][tag]


def _regular_options(
    pot: list[DrawEntry],
    shows: list[ShowState],
    rng: random.Random,
    balance_ceils: dict[str, dict] | None,
) -> list[list[tuple[ShowState, DrawEntry]]]:
    entries = pot[:]
    rng.shuffle(entries)
    options: list[list[tuple[ShowState, DrawEntry]]] = []

    def visit(index: int, used_shows: set[str], placements: list[tuple[ShowState, DrawEntry]]):
        if index == len(entries):
            options.append(placements[:])
            return

        entry = entries[index]
        candidates = [
            show
            for show in shows
            if show.name not in used_shows and _can_place_regular(show, entry, balance_ceils)
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


def _assign_single_pot(entries: list[DrawEntry], shows: list[ShowState], rng: random.Random):
    by_country: dict[str, list[DrawEntry]] = defaultdict(list)
    for entry in entries:
        by_country[entry.code].append(entry)

    dup_groups = [group for group in by_country.values() if len(group) > 1]
    singletons = [group[0] for group in by_country.values() if len(group) == 1]
    dup_groups.sort(key=len, reverse=True)
    for group in dup_groups:
        rng.shuffle(group)
    rng.shuffle(singletons)

    def score(show: ShowState, entry: DrawEntry):
        balance_count = sum(
            show.balance_counts[key][entry.tag(key)]
            for key in BALANCE_KEYS
            if entry.tag(key)
        )
        return (
            Counter(e.code for e in show.entries)[entry.code] * 1_000_000
            + balance_count * 1_000
            - (show.limit - len(show.entries)) * 10
            + rng.random()
        )

    def pick(entry: DrawEntry):
        candidates = [
            show
            for show in shows
            if len(show.entries) < show.limit and entry.submitter not in show.submitters
        ]
        if not candidates:
            raise ValueError(
                f"Cannot place entry from {entry.code}/{entry.submitter}: every show is full "
                "or already has this submitter"
            )
        candidates.sort(key=lambda show: score(show, entry))
        return candidates[0]

    for group in dup_groups:
        for entry in group:
            _place(pick(entry), entry, track_pot=False)

    for entry in singletons:
        _place(pick(entry), entry, track_pot=False)


def _conflicts(a: DrawEntry | None, b: DrawEntry | None):
    if not a or not b:
        return False
    return (
        a.code == b.code
        or a.submitter == b.submitter
        or any(a.tag(key) and a.tag(key) == b.tag(key) for key in BALANCE_KEYS)
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

    return [entry for entry in result if entry is not None]


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
        ShowState(name=name, limit=limit)
        for name, limit in zip(show_names, show_limits, strict=True)
    ]
    draw_pots = [
        [DrawEntry(data=dict(entry), pot=pot) for entry in entries]
        for pot, entries in pots.items()
    ]

    if single_pot:
        _assign_single_pot([entry for pot in draw_pots for entry in pot], shows, rng)
    else:
        if any(len(pot) > len(shows) for pot in draw_pots):
            raise ValueError("A pot has more entries than there are semifinal shows")
        all_entries = [entry for pot in draw_pots for entry in pot]
        balance_ceils = {key: _ceiling_by_key(all_entries, key, len(shows)) for key in BALANCE_KEYS}
        try:
            _assign_regular(draw_pots, shows, rng, balance_ceils)
        except ValueError:
            for show in shows:
                show.entries.clear()
                show.submitters.clear()
                show.pots.clear()
                show.codes.clear()
                show.balance_counts = {key: Counter() for key in BALANCE_KEYS}
            _assign_regular(draw_pots, shows, rng, None)

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
    draw_entries = [
        DrawEntry(data=dict(entry), pot=entry.get("pot") or 0)
        for entry in entries
    ]
    rng.shuffle(draw_entries)
    return [entry.data for entry in spread_running_order(draw_entries, rng)]
