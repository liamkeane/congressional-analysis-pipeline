"""
backfill/config.py

Scope for the historical backfill: which congresses, which entities, and
which per-bill / per-amendment sub-resources to pull.

Request budget is what bounds this. The client holds to ~4,500 requests/hour,
and every bill costs 1 detail request + 1 per sub-resource (more if a
sub-resource paginates). A congress has roughly 15-20k bills, so with the
default four bill sub-resources that's ~5 requests x ~18k bills = ~90k
requests, or ~20 hours per congress for bills alone. Trim `bill_types` /
`bill_subresources` to go faster; runs are resumable either way.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from landing.writer import landing_root

ENTITIES = ("bills", "amendments", "members", "committees", "house_votes")

BILL_TYPES = ("hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres")

BILL_SUBRESOURCES = (
    "actions",
    "amendments",
    "committees",
    "cosponsors",
    "related_bills",
    "subjects",
    "summaries",
    "text",
    "titles",
)

AMENDMENT_SUBRESOURCES = ("actions", "amendments", "cosponsors", "text")


@dataclass(frozen=True)
class BackfillConfig:
    # Recent congresses only -- earlier eras have sparser subject/cosponsor
    # data (see docs/considerations.md #5), and house-vote data starts at 118.
    congresses: tuple[int, ...] = (118, 119)
    entities: tuple[str, ...] = ENTITIES
    # None pulls every bill type via /bill/{congress}.
    bill_types: Optional[tuple[str, ...]] = None
    # What the planned analyses need: velocity (actions), committee
    # bottlenecks (committees), bipartisanship + network (cosponsors),
    # policy attention (subjects).
    bill_subresources: tuple[str, ...] = ("actions", "committees", "cosponsors", "subjects")
    amendment_subresources: tuple[str, ...] = ("actions", "cosponsors")
    include_vote_members: bool = True
    # Cap on items taken from each list endpoint -- for smoke tests.
    limit: Optional[int] = None
    landing_root: Path = field(default_factory=landing_root)

    def __post_init__(self) -> None:
        _check_subset("entities", self.entities, ENTITIES)
        _check_subset("bill_subresources", self.bill_subresources, BILL_SUBRESOURCES)
        _check_subset(
            "amendment_subresources", self.amendment_subresources, AMENDMENT_SUBRESOURCES
        )
        if self.bill_types is not None:
            _check_subset("bill_types", self.bill_types, BILL_TYPES)
        if not self.congresses:
            raise ValueError("congresses must not be empty")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be positive")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["landing_root"] = str(self.landing_root)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BackfillConfig":
        data = dict(data)
        for key in ("congresses", "entities", "bill_subresources", "amendment_subresources"):
            data[key] = tuple(data[key])
        if data.get("bill_types") is not None:
            data["bill_types"] = tuple(data["bill_types"])
        data["landing_root"] = Path(data["landing_root"])
        return cls(**data)


def _check_subset(name: str, values: tuple, allowed: tuple) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValueError(f"Unknown {name}: {unknown}. Allowed: {list(allowed)}")
