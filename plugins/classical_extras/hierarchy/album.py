# -*- coding: utf-8 -*-
"""Album level model: what a recording performs, and how tracks are grouped.

The work graph in :mod:`hierarchy.graph` says how works are related to each
other. It says nothing about which works a *recording* performs, because
that is a fact about the release, not about the works.

This module adds that missing half:

* :class:`Performance` - one recording's performance of one or more works.
* :func:`group_names` - the shuffle key of every track of an album.

Like the rest of the package this module is free of any Picard dependency.
Names are plain strings, passed in as data.

Why a recording may perform several works
-----------------------------------------
MusicBrainz allows a recording to be related to more than one work, and the
releases in the fixtures do it in two ways.

*One work twice.* Daphnis' tracks carry both a 'partial' performance
relation and a plain one, and both point at the same work. The recording
performs the work only in part, so the legacy wrote the *recording* id into
the work id slot and treated the recording as a node one level below the
work. The recording id stands in for the work in that node.

*Several works.* Planets' track 7 performs both suites' "Neptune". The
works are peers, so they belong in one node together, and the node's parent
is the union of their parents.

Both cases are the same rule: **the works a recording performs form one
node**, and the node's parent is the union of the contributions of those
works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence

from .graph import NodeId, WorkId


@dataclass(frozen=True)
class Performance:
    """One recording's performance of one or more works.

    ``key`` identifies the performance - the recording id. It stands in for a
    work that is only partially performed, because such a recording is not
    the work itself.

    ``works`` are all the works the recording is related to. ``partial`` are
    those it performs only in part and ``complete`` those it performs in
    full. A work may be in both sets: daphnis' tracks carry a 'partial'
    performance relation *and* a plain one, both pointing at the same work.
    The two sets are therefore kept apart rather than derived from each
    other, because the two cases differ:

    * a work only partially performed contributes nothing to the node's
      parent - the recording is not that work and does not stand in for it;
    * a work performed both in full and in part contributes itself, because
      the recording id stands in for the work one level below it.

    The 'partial' attribute only means anything when the plugin's
    ``cwp_partial`` option is on. With the option off the attribute is
    ignored and a partially performed work is treated as an ordinary one -
    the legacy's behaviour, and the one the Gayne album's tags show.
    """

    key: WorkId
    works: FrozenSet[WorkId] = frozenset()
    partial: FrozenSet[WorkId] = frozenset()
    complete: FrozenSet[WorkId] = frozenset()

    def __post_init__(self) -> None:
        unknown = (self.partial | self.complete) - self.works
        if unknown:
            raise ValueError(
                "works that are not performed: %s"
                % ", ".join(sorted(w.mbid for w in unknown)))

    def member(self, work: WorkId) -> WorkId:
        """The id standing for ``work`` inside this performance's node.

        A work that is partially performed is represented by the recording
        id, because the recording is not the work.
        """
        return self.key if work in self.partial else work

    @property
    def members(self) -> FrozenSet[WorkId]:
        """The ids this performance's node holds."""
        return frozenset(self.member(work) for work in self.works)

    def contribution(self, work: WorkId) -> str:
        """What ``work`` contributes to the node's parent.

        ``'self'`` when the work is performed in full *and* in part, so that
        it sits one level above the recording id. ``'parents'`` when the
        work is performed in full only. ``'none'`` when it is performed only
        in part.
        """
        if work in self.complete and work in self.partial:
            return 'self'
        if work in self.complete:
            return 'parents'
        return 'none'

    def without_partial(self) -> "Performance":
        """This performance with the 'partial' attribute ignored.

        The partially performed work becomes an ordinary one, which is what
        the legacy does when its ``cwp_partial`` option is off.
        """
        if not self.partial:
            return self
        return Performance(
            key=self.key,
            works=self.works,
            partial=frozenset(),
            complete=self.works)


@dataclass
class Album:
    """The performances of one release, and the groups they form.

    ``performances`` are the recordings of the album, in the order they were
    seen. ``names`` gives the title of each work.
    """

    performances: List[Performance] = field(default_factory=list)
    names: Dict[WorkId, str] = field(default_factory=dict)

    def name_of(self, work: WorkId) -> str:
        """The title of a work, or an empty string when it is unknown."""
        return self.names.get(work, '')

    def group_names(
            self,
            candidates: Mapping[WorkId, FrozenSet[WorkId]]) -> Dict[WorkId, str]:
        """The shuffle key of every recording.

        ``candidates`` maps a performance's key to the works that could be
        its top: the top works its chain reaches. A recording whose
        candidates share one name has nothing to choose - the name is the
        group. A recording with several candidate names takes the one the
        album as a whole prefers.

        The preference is a vote: every recording of the album gives one vote
        to each of its *distinct* candidate names, and the name with the most
        votes wins. A tie is broken by the lowest normalised name, so every
        recording of the album yields the identical string and sibling tracks
        are never split apart.
        """
        votes: Dict[str, int] = {}
        for key, tops in candidates.items():
            for name in self._candidate_names(tops):
                votes[name] = votes.get(name, 0) + 1

        groups: Dict[WorkId, str] = {}
        for key, tops in candidates.items():
            names = sorted(self._candidate_names(tops))
            if not names:
                groups[key] = ''
                continue
            best = max(votes.get(name, 0) for name in names)
            winners = [name for name in names if votes.get(name, 0) == best]
            groups[key] = min(winners, key=normalise_name)
        return groups

    def _candidate_names(self, tops: FrozenSet[WorkId]) -> List[str]:
        """The distinct names of the given top works, in discovery order."""
        names: List[str] = []
        for top in sorted(tops, key=lambda w: w.mbid):
            name = self.name_of(top)
            if name and name not in names:
                names.append(name)
        return names


def normalise_name(name: str | None) -> str:
    """Normalise a work name for duplicate comparison.

    Lower case, whitespace collapsed, surrounding punctuation dropped. This
    matches the legacy's ``PartLevels._normalise_name``, so a tie is broken
    the same way.
    """
    if name is None:
        return ''
    return re.sub(r'\s+', ' ', str(name).strip().lower()).strip('.,;:"\'')


def group_of(candidates: Mapping[WorkId, FrozenSet[WorkId]],
             names: Mapping[WorkId, str]) -> Dict[WorkId, str]:
    """The shuffle key of every recording, given the candidate top works.

    A convenience wrapper around :meth:`Album.group_names` for callers that
    have the candidates and the names but no album object.
    """
    album = Album(names=dict(names))
    return album.group_names(candidates)


def chains_of(
        hierarchy,
        performances: Iterable[Performance]) -> Dict[WorkId, List[NodeId]]:
    """The chain of every performance, from its node up to a top.

    A performance that performs several works has one node holding all of
    them, so it has one chain - which is the whole point of the fusion.
    """
    chains: Dict[WorkId, List[NodeId]] = {}
    for performance in performances:
        node = hierarchy.node_of_performance.get(performance.key)
        if node is None:
            chains[performance.key] = []
            continue
        chains[performance.key] = hierarchy.path_to_top(node)
    return chains


def candidate_tops(
        chains: Mapping[WorkId, Sequence[NodeId]],
        hierarchy) -> Dict[WorkId, FrozenSet[WorkId]]:
    """The top works every chain reaches.

    A chain's top node may hold several works - the two editions of Swan
    Lake, for instance - and all of them are candidates, because the group
    is chosen by name and the names may differ.
    """
    candidates: Dict[WorkId, FrozenSet[WorkId]] = {}
    for key, chain in chains.items():
        if not chain:
            candidates[key] = frozenset()
            continue
        candidates[key] = hierarchy.nodes[chain[-1]].works
    return candidates
