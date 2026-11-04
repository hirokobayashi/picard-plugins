"""The work metadata the genre and date tags are made from.

MusicBrainz's work lookup carries the folksonomy tags, the work type and
the dated relations that tell when a work was composed, published and
premiered. The legacy reads each of those from the work response with
its generic ``parse_data`` walker and stores them against the work; the
model does the same extraction in one place, from the same responses the
hierarchy is built from.

A date pair is written as one year when the relation's begin and end
year are the same and as a ``begin-end`` range when they differ - the
legacy's ``year`` + ``DATE_SEP`` formatting. A relation without dates
contributes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping


def _year(date: Any) -> List[str]:
    """The year portion of a date or list of dates, blank when absent."""
    if isinstance(date, list):
        return [(d or "").split("-")[0] for d in date]
    return [(date or "").split("-")[0]]


def _date_pairs(
        relations: List[Mapping[str, Any]], target: str, rtype: str) -> List[str]:
    """The begin/end years of every relation of the given target and
    type, each as a year or a ``begin-end`` range. An entry that is not
    an object carries no dates."""
    pairs = [r for r in relations
             if isinstance(r, Mapping)
             and r.get("target-type") == target
             and r.get("type") == rtype]
    begins = _year([r.get("begin") for r in pairs])
    ends = _year([r.get("end") for r in pairs])
    if begins == ends:
        return [b for b in begins if b]
    return [(b + "-" + e if b != e else b)
            for b, e in zip(begins, ends) if b]


@dataclass
class WorkMeta:
    """The genre and date material of one work lookup.

    ``folks_genres`` are the work's folksonomy tags; ``worktype_genres``
    is the work's type (Ballet, Overture, ...). The three date lists
    come from the work's composer, publishing and premiere relations.
    """

    folks_genres: List[str] = field(default_factory=list)
    worktype_genres: List[str] = field(default_factory=list)
    composed_dates: List[str] = field(default_factory=list)
    published_dates: List[str] = field(default_factory=list)
    premiered_dates: List[str] = field(default_factory=list)

    @classmethod
    def of(cls, response: Mapping[str, Any]) -> "WorkMeta":
        """The metadata of one work response."""
        relations = response.get("relations") or []
        worktype = ([response["type"]] if response.get("type") else [])
        tags = response.get("tags") or []
        return cls(
            folks_genres=[t.get("name") for t in tags
                          if isinstance(t, Mapping)],
            worktype_genres=worktype,
            composed_dates=_date_pairs(relations, "artist", "composer"),
            published_dates=_date_pairs(relations, "label", "publishing"),
            premiered_dates=_date_pairs(relations, "place", "premiere"),
        )


def work_metadata(response: Mapping[str, Any]) -> WorkMeta:
    """The metadata of one work response (the functional spelling)."""
    return WorkMeta.of(response)
