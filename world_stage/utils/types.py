import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache, total_ordering


@dataclass
class ShowData:
    id: int
    points: list[int]
    point_system_id: int
    name: str
    short_name: str
    voting_opens: datetime.datetime | None
    voting_closes: datetime.datetime | None
    predictions_close: datetime.datetime | None
    year: int
    dtf: int | None
    sc: int | None
    special: int | None
    status: str


@dataclass(frozen=True)
class UserPermissions:
    role: str = "none"
    can_edit: bool = False
    can_view_restricted: bool = False

    def __str__(self) -> str:
        return self.role


@dataclass(kw_only=True)
class Country:
    cc: str
    name: str
    is_participating: bool
    cc3: str
    flag_variant: str | None = None


@dataclass
class Year:
    id: int
    special_name: str | None = None
    special_short_name: str | None = None
    status: str | None = None


@total_ordering
@dataclass
class VoteData:
    ro: int
    total_votes: int | None
    max_pts: int | None
    show_voters: int | None
    sum: int = 0
    count: int = 0
    # Penalty subtracted from the raw score for this song in this show
    # (e.g. submitter failed to vote). ``sum`` already reflects the
    # post-penalty total; this field is just for display.
    penalty: int = 0
    pts: dict[int, int] = field(default_factory=lambda: defaultdict(int))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            raise TypeError("Cannot compare VoteData with non-VoteData object")
        if self.sum != other.sum:
            return self.sum < other.sum
        if self.count != other.count:
            return self.count < other.count
        this_keys = sorted(self.pts.keys())
        other_keys = sorted(other.pts.keys())
        this_max = max(this_keys, default=0)
        other_max = max(other_keys, default=0)
        if this_max != other_max:
            return this_max < other_max
        overall_max = max(this_max, other_max)
        for i in range(overall_max, 0, -1):
            this_v = self.pts.get(i, 0)
            other_v = other.pts.get(i, 0)
            if this_v != other_v:
                return this_v < other_v
        if self.ro is None or other.ro is None:
            return False
        return self.ro > other.ro

    def __str__(self):
        return f"VoteData(ro={self.ro}, count={self.count}, pts={self.pts})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            return False
        return (
            self.ro == other.ro
            and self.sum == other.sum
            and self.count == other.count
            and self.pts == other.pts
        )

    def pct(self) -> str:
        if not self.show_voters or not self.max_pts:
            return "0.00%"
        return f"{(self.sum / (self.show_voters * self.max_pts)) * 100:.2f}%"

    def get_pt(self, pt: int) -> int:
        if pt not in self.pts:
            return 0
        return self.pts[pt]

    def as_dict(self) -> dict:
        return {
            "ro": self.ro,
            "total_votes": self.total_votes,
            "max_pts": self.max_pts,
            "show_voters": self.show_voters,
            "sum": self.sum,
            "count": self.count,
            "pts": dict(self.pts),
        }


@dataclass(frozen=True)
class Language:
    name: str = ""
    tag: str = ""
    extlang: str | None = None
    region: str | None = None
    subvariant: str | None = None
    suppress_script: str | None = None

    @lru_cache  # noqa: B019 — Language is a frozen dataclass, so cached self is immutable
    def str(self, script: str | None = None, cc: str | None = None) -> str:
        components = [self.tag]
        if self.extlang:
            components.append(self.extlang)
        if script and script != self.suppress_script:
            components.append(script)
        if cc:
            components.append(cc.upper())
        if self.subvariant:
            components.append(self.subvariant)
        return "-".join(components)

    def as_dict(self):
        return {
            "name": self.name,
            "tag": self.tag,
            "extlang": self.extlang,
            "region": self.region,
            "subvariant": self.subvariant,
            "suppress_script": self.suppress_script,
        }


@total_ordering
@dataclass
class Show:
    year: int | None
    short_name: str
    name: str
    date: datetime.date

    def __init__(self, *, year: int | None, short_name: str, name: str, date: datetime.date):
        self.year = year
        self.short_name = short_name
        self.name = name
        self.date = date

    def __lt__(self, other):
        def value_map(name: str) -> int:
            if name.startswith("sf"):
                return 0
            elif name == "sc":
                return 1
            elif name == "f":
                return 2
            else:
                return 3

        if not isinstance(other, Show):
            return NotImplemented

        if self.year is None or other.year is None:
            return self.date < other.date

        if self.year != other.year:
            return self.year < other.year

        v1 = value_map(self.short_name)
        v2 = value_map(other.short_name)
        return v1 < v2
