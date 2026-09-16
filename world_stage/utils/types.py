import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
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
    date: datetime.datetime | None
    year: int
    status: str
    voting_ruleset_version: str
    revote_ruleset_version: str
    penalizes_non_voters: bool
    revote_penalizes_non_voters: bool
    local_short_name: str
    national_final_id: int | None
    national_final_short_name: str | None
    national_final_name: str | None
    national_final_owner_id: int | None
    national_final_country_id: str | None
    national_final_status: str | None
    progressions: list[dict]
    point_system: dict

    @property
    def contextual_name(self) -> str:
        if self.national_final_name:
            return f"{self.national_final_name}: {self.name}"
        return self.name

    @property
    def total_qualifiers(self) -> int:
        return sum(edge["qualifier_count"] for edge in self.progressions)

    @property
    def primary_qualifiers(self) -> int:
        return self.progressions[0]["qualifier_count"] if self.progressions else 0

    def qualifier_class(
        self,
        entry_status: str | None,
        first: str = "direct-to-final",
        later: str = "second-chance",
        non_qualifier: str = "non-qualifier",
    ) -> str:
        """Map a stored progression destination to the existing result styles."""
        if entry_status == "nq":
            return non_qualifier
        for index, progression in enumerate(self.progressions):
            if progression["target_short_name"] == entry_status:
                return first if index == 0 else later
        return ""


@dataclass(frozen=True)
class UserPermissions:
    role: str = "none"
    can_edit: bool = False
    can_view_restricted: bool = False

    def __str__(self) -> str:
        return self.role

    @property
    def can_moderate(self) -> bool:
        """Whether the account may handle verifications and moderator messages."""
        return self.role == "editor" or self.can_view_restricted


def can_manage_show(
    show: ShowData, user: tuple[int, str] | None, permissions: UserPermissions
) -> bool:
    """Whether this caller may manage and preview this particular show."""
    return permissions.can_view_restricted or bool(
        user is not None
        and show.national_final_owner_id is not None
        and user[0] == show.national_final_owner_id
    )


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
    max_possible_points: int | None = None
    points_percentage: Decimal | None = None
    adjusted_max_possible_points: int | None = None
    points_midpoint: Decimal | None = None
    adjusted_points_percentage: Decimal | None = None

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, VoteData):
            raise TypeError("Cannot compare VoteData with non-VoteData object")
        if self.sum != other.sum:
            return self.sum < other.sum
        if self.count != other.count:
            return self.count < other.count
        for points in sorted(self.pts.keys() | other.pts.keys(), reverse=True):
            this_v = self.pts.get(points, 0)
            other_v = other.pts.get(points, 0)
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

    def adjusted_pct(self) -> str:
        if self.adjusted_points_percentage is None:
            return "0.00%"
        return f"{self.adjusted_points_percentage:.2f}%"

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
            "max_possible_points": self.max_possible_points,
            "points_percentage": self.points_percentage,
            "adjusted_max_possible_points": self.adjusted_max_possible_points,
            "points_midpoint": self.points_midpoint,
            "adjusted_points_percentage": self.adjusted_points_percentage,
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
    date: datetime.datetime | None

    def __init__(
        self,
        *,
        year: int | None,
        short_name: str,
        name: str,
        date: datetime.datetime | None,
    ):
        self.year = year
        self.short_name = short_name
        self.name = name
        self.date = date

    def __lt__(self, other):
        if not isinstance(other, Show):
            return NotImplemented

        if self.year is None or other.year is None:
            latest = datetime.datetime.max.replace(tzinfo=datetime.UTC)
            return (self.date or latest) < (other.date or latest)

        if self.year != other.year:
            return self.year < other.year

        return (
            self.date or datetime.datetime.max.replace(tzinfo=datetime.UTC),
            self.short_name,
        ) < (
            other.date or datetime.datetime.max.replace(tzinfo=datetime.UTC),
            other.short_name,
        )
