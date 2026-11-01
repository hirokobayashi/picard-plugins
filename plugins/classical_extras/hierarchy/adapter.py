# -*- coding: utf-8 -*-
"""The adapter between MusicBrainz's JSON and the work-hierarchy model.

The model in :mod:`hierarchy.graph`, :mod:`hierarchy.project` and
:mod:`hierarchy.album` knows nothing about MusicBrainz's wire format and
nothing about tags. This module is the seam between the two: it reads the
JSON the web service returns, and it turns the model's answer into the
tag values the plugin writes.

Like the rest of the package it is free of any Picard dependency, so it
can be tested against recorded responses with no Picard running.

What the legacy does, and what this does
-----------------------------------------
The legacy keyed a hierarchy node by a *tuple* of work ids and filled
those tuples as it walked the responses. The order of the ids in the
tuple was the order the relations came back in, and the tags carried
that order.

Here a node is identified by the *set* of works it stands for, so the
order carries no meaning. The adapter therefore has to restore the
order explicitly when it writes the ids, or the tags would change even
where the hierarchy is the same.

The rule, measured against the legacy over every fixture:

* the level-0 ids are the recording's **performance** relations, in the
  order the relations came back in;
* a work that is only *partially* performed is represented by the
  **recording id** instead of the work id, in the relation's place;
* a work that is related twice - once partially, once in full - is
  represented by the recording id alone, because the recording is not the
  work;
* the 'partial' substitution only applies when the plugin's
  ``cwp_partial`` option is on.

The levels above the first are the node's works in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence, Tuple

from .album import Album, Performance, candidate_tops, chains_of
from .graph import NodeId, WorkGraph, WorkId
from .project import Hierarchy, project

#: The separator the legacy uses between the ids of one level.
ID_SEPARATOR = '; '


def format_ids(works: Iterable[WorkId]) -> str:
    """The ids of one level, as the legacy writes them.

    The legacy keys a level by a tuple of work ids and writes the tuple's
    repr, so a level of one id reads ``('abc...',)`` and a level of two
    reads ``('abc...', 'def...')``. The order inside the tuple is the
    order the relations came back in.
    """
    ids = list(works)
    if len(ids) == 1:
        return "('%s',)" % ids[0].mbid
    return '(%s)' % ', '.join("'%s'" % work.mbid for work in ids)


@dataclass
class WorkNames:
    """The title of each work, and the aliases it is known by."""

    titles: Dict[WorkId, str] = field(default_factory=dict)

    def title_of(self, work: WorkId) -> str:
        """The title of a work, or an empty string when it is unknown."""
        return self.titles.get(work, '')

    def names_of(self, works: Iterable[WorkId]) -> List[str]:
        """The titles of the given works, in the order given.

        A title is only given once, even when two works share it. The
        legacy's top work tag holds the *names*, and two works with the
        same name are one name to the reader - the Swan Lake editions
        are both called "Swan Lake, op. 20", and the tag says so once.
        """
        names: List[str] = []
        for work in works:
            title = self.title_of(work)
            if title and title not in names:
                names.append(title)
        return names


@dataclass
class WorkRelations:
    """The resolved parents of each work, in the order they came back in.

    The legacy's node keys are tuples, and the order inside a tuple is
    the order the relations came back in. The model holds a node's works
    as a set, so the order has to be restored here to write the same tag.

    The parents are the *resolved* ones - the graph's own ordered
    fallback of ``parts``, ``arrangement`` and ``medley`` - because that
    is the set the hierarchy is built from. A work reached only by an
    ``arrangement`` relation, like the Tristan album's "Prelude and
    Liebestod", has its parents named by that relation, and the order of
    those names is the order the legacy wrote them in.
    """

    #: The resolved parent ids of each work, in relation order.
    parents: Dict[WorkId, List[WorkId]] = field(default_factory=dict)

    def order_of(
            self,
            works: Iterable[WorkId],
            through: WorkId | None = None) -> List[WorkId]:
        """The given works in the order the relation gave them.

        The legacy's node holds the parents of the work it was reached
        through, in the order that work's own relation gave them. So the
        order comes from ``through`` - the work the level was reached
        by - and not from the order the works happen to be in.

        With no work given, the order is looked for in every relation,
        which is what a caller that only knows the works themselves can
        do.

        A work that no relation names keeps its place at the end, in work
        id order, so the result is always deterministic.
        """
        wanted = list(works)
        order: Dict[WorkId, int] = {}
        if through is not None:
            candidates = [self.parents.get(through, ())]
        else:
            candidates = list(self.parents.values())
        for parent_list in candidates:
            for position, parent in enumerate(parent_list):
                if parent in wanted and parent not in order:
                    order[parent] = position
        return sorted(
            wanted,
            key=lambda work: (order.get(work, len(order)), work.mbid))


def read_work_graph(
        works: Iterable[Mapping],
        use_arrangements: bool = True,
        use_medleys: bool = True,
        use_collections: bool = True) -> Tuple[WorkGraph, WorkRelations]:
    """Build a work graph from MusicBrainz's work responses.

    Each entry is one ``/ws/2/work`` response. The relations it carries
    become the graph's edges: a ``parts`` relation pointing backwards is
    a parent edge, and everything else is retained but takes no part in
    the hierarchy.

    The order the parents came back in is returned as well, because the
    legacy's node keys are tuples and the order inside a tuple is the
    order the relations came back in. The parents are the *resolved*
    ones - the graph's own ordered fallback of ``parts``,
    ``arrangement`` and ``medley`` - because that is the set the
    hierarchy is built from.
    """
    graph = WorkGraph()
    for response in works:
        source = WorkId(response['id'])
        graph.add_work(source)
        for relation in response.get('relations') or ():
            target = relation.get('work')
            if not target or not target.get('id'):
                continue
            graph.add_relation(
                source,
                relation.get('type'),
                relation.get('direction'),
                WorkId(target['id']),
                relation.get('attributes') or ())
    resolved = graph.resolve_parents_ordered(
        use_arrangements=use_arrangements,
        use_medleys=use_medleys,
        use_collections=use_collections)
    relations = WorkRelations(
        parents={
            work: list(parents)
            for work, parents in resolved.items()
            if parents})
    return graph, relations


def read_performance(
        recording: Mapping,
        use_partial: bool = True) -> Performance:
    """Build a performance from a recording's relations.

    The recording's **performance** relations name the works it performs.
    A relation marked 'partial' says the recording performs the work only
    in part, and the recording id stands in for the work.

    A work may be related twice - once partially and once in full. Then
    the recording stands in for the work, and the work itself still
    contributes: it is the parent of the recording id one level up. So
    such a work is in both sets.

    ``use_partial`` is the plugin's ``cwp_partial`` option: with it off
    the attribute is ignored and a partially performed work is an ordinary
    one.
    """
    key = WorkId(recording['id'])
    works: List[WorkId] = []
    partial: List[WorkId] = []
    complete: List[WorkId] = []
    for relation in recording.get('relations') or ():
        if relation.get('type') != 'performance':
            continue
        target = relation.get('work')
        if not target or not target.get('id'):
            continue
        work = WorkId(target['id'])
        if work in works:
            # the work is related again: a second relation to the same
            # work makes it performed in full as well
            if use_partial and 'partial' in (
                    relation.get('attributes') or ()):
                if work not in partial:
                    partial.append(work)
            elif work not in complete:
                complete.append(work)
            continue
        works.append(work)
        if use_partial and 'partial' in (relation.get('attributes') or ()):
            partial.append(work)
        else:
            complete.append(work)
    return Performance(
        key=key,
        works=frozenset(works),
        partial=frozenset(partial),
        complete=frozenset(complete))


@dataclass
class Recording:
    """A recording, and the ids its first level holds.

    ``performance`` is the model object. ``members`` are the ids of the
    recording's first level, in the legacy's order: the performance
    relations in the order they came back in, with a partially performed
    work represented by the recording id.
    """

    performance: Performance
    members: List[WorkId] = field(default_factory=list)


@dataclass
class TrackTags:
    """The hierarchy tags of one track."""

    #: The work ids of each level, level 0 first.
    levels: List[List[WorkId]] = field(default_factory=list)
    #: The top work's ids.
    top: List[WorkId] = field(default_factory=list)
    #: The top work's name or names.
    top_names: List[str] = field(default_factory=list)
    #: The group name - the shuffle key, always one value.
    group: str = ''
    #: The number of levels below this track's own level.
    part_levels: int = 0
    #: The number of levels in the whole hierarchy of this track's top.
    work_part_levels: int = 0

    def id_string(self, level: int) -> str:
        """The work ids of one level, as the legacy writes them."""
        return format_ids(self.levels[level])

    def as_metadata(self) -> Dict[str, str]:
        """The tags, in the legacy's names."""
        metadata: Dict[str, str] = {}
        for level, works in enumerate(self.levels):
            metadata['~cwp_workid_%d' % level] = self.id_string(level)
        metadata['~cwp_workid_top'] = format_ids(self.top)
        if len(self.top_names) == 1:
            metadata['~cwp_work_top'] = self.top_names[0]
        elif self.top_names:
            metadata['~cwp_work_top'] = ID_SEPARATOR.join(self.top_names)
        metadata['~cwp_work_group'] = self.group
        metadata['~cwp_part_levels'] = str(self.part_levels)
        metadata['~cwp_work_part_levels'] = str(self.work_part_levels)
        return metadata


def node_height(hierarchy: Hierarchy, node: NodeId) -> int:
    """The longest downward path from a node.

    This is the number of levels below the node, which is what the
    legacy's ``level_calc`` computes for the whole tree of a top work.
    """
    best = 0
    stack = [(node, 0)]
    seen = set()
    while stack:
        current, depth = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        best = max(best, depth)
        for child in hierarchy.nodes[current].children:
            stack.append((child, depth + 1))
    return best


class AlbumAdapter:
    """Turns a release's responses into the hierarchy tags of its tracks.

    The adapter is given the work responses and the recording responses
    of one release, and produces the tags of every track.
    """

    def __init__(
            self,
            works: Iterable[Mapping],
            recordings: Iterable[Mapping],
            names: WorkNames | None = None,
            use_partial: bool = True,
            use_arrangements: bool = True,
            use_medleys: bool = True,
            use_collections: bool = True) -> None:
        self.graph, self.relations = read_work_graph(
            works,
            use_arrangements=use_arrangements,
            use_medleys=use_medleys,
            use_collections=use_collections)
        self.names = names or WorkNames()
        self.use_partial = use_partial
        self.use_arrangements = use_arrangements
        self.use_medleys = use_medleys
        self.use_collections = use_collections
        self.recordings = [
            self._read_recording(recording) for recording in recordings]

    @property
    def performances(self) -> List[Performance]:
        """The model's performance of every recording."""
        return [recording.performance for recording in self.recordings]

    def _read_recording(self, recording: Mapping) -> Recording:
        """One recording: its performance and its first level's ids."""
        performance = read_performance(
            recording, use_partial=self.use_partial)
        return Recording(
            performance=performance,
            members=self._ordered_members(recording, performance))

    def _ordered_members(
            self,
            recording: Mapping,
            performance: Performance) -> List[WorkId]:
        """The recording's first level ids, in the legacy's order.

        The performance relations in the order they came back in, with a
        partially performed work represented by the recording id in the
        relation's place. A work related twice - once partially, once in
        full - is represented by the recording id alone, because the
        recording is not the work.
        """
        key = performance.key
        members: List[WorkId] = []
        seen: Dict[WorkId, int] = {}
        for relation in recording.get('relations') or ():
            if relation.get('type') != 'performance':
                continue
            target = relation.get('work')
            if not target or not target.get('id'):
                continue
            work = WorkId(target['id'])
            partial = (
                self.use_partial
                and 'partial' in (relation.get('attributes') or ()))
            if work in seen:
                if partial:
                    # the work is partially performed as well, so the
                    # recording id replaces the work's own id
                    members[seen[work]] = key
                continue
            seen[work] = len(members)
            members.append(key if partial else work)
        return members

    def build(self) -> Tuple[Hierarchy, Dict[WorkId, str]]:
        """The hierarchy and the group of every recording."""
        hierarchy = project(
            self.graph,
            use_arrangements=self.use_arrangements,
            use_medleys=self.use_medleys,
            use_collections=self.use_collections,
            performances=self.performances)
        chains = chains_of(hierarchy, self.performances)
        album = Album(names=self.names.titles)
        groups = album.group_names(candidate_tops(chains, hierarchy))
        return hierarchy, groups

    def tags(self) -> Dict[WorkId, TrackTags]:
        """The hierarchy tags of every recording."""
        hierarchy, groups = self.build()
        chains = chains_of(hierarchy, self.performances)
        candidates = candidate_tops(chains, hierarchy)
        votes = self._votes(candidates)
        result: Dict[WorkId, TrackTags] = {}
        for recording in self.recordings:
            chain = chains.get(recording.performance.key) or []
            if not chain:
                continue
            result[recording.performance.key] = self._tags_for(
                recording, chain, hierarchy,
                groups.get(recording.performance.key, ''),
                votes)
        return result

    def _votes(
            self,
            candidates: Mapping[WorkId, FrozenSet[WorkId]]) -> Dict[str, int]:
        """How many tracks of the album have each name as a candidate top.

        The legacy votes over the *names* of a track's candidate top
        works, and the winner becomes the track's top work tag.
        """
        votes: Dict[str, int] = {}
        for works in candidates.values():
            for name in set(self.names.names_of(works)):
                votes[name] = votes.get(name, 0) + 1
        return votes

    def _top_names(
            self,
            top: Sequence[WorkId],
            votes: Mapping[str, int]) -> List[str]:
        """The names of a track's top work.

        The legacy picks the candidate name the most tracks of the album
        voted for, so that every track sharing a top names it the same
        way. A tie keeps every tied name.
        """
        names = self.names.names_of(top)
        if not names:
            return []
        if not votes:
            return names[:1]
        best = max(votes.get(name, 0) for name in names)
        winners = [name for name in names if votes.get(name, 0) == best]
        return winners

    def _tags_for(
            self,
            recording: Recording,
            chain: Sequence[NodeId],
            hierarchy: Hierarchy,
            group: str,
            votes: Mapping[str, int]) -> TrackTags:
        """The tags of one recording, from its chain.

        ``~cwp_part_levels`` is the number of levels between the track and
        the top - how deep the track's own work sits in the hierarchy.
        ``~cwp_work_part_levels`` is the depth of the whole hierarchy
        under this track's top, which is the same for every track that
        shares the top.
        """
        levels = [self._level_ids(recording, node, index, through)
                  for index, (node, through) in enumerate(
                      self._levels_with_through(recording, chain))]
        top = levels[-1]
        return TrackTags(
            levels=levels,
            top=top,
            top_names=self._top_names(top, votes),
            group=group,
            part_levels=len(chain) - 1,
            work_part_levels=node_height(hierarchy, chain[-1]))

    def _levels_with_through(
            self,
            recording: Recording,
            chain: Sequence[NodeId]) -> List[Tuple[NodeId, WorkId | None]]:
        """Each level of the chain with the work it was reached through.

        A level's order is the order the relation of the work below it
        gave, so every level needs to know which work it hangs off. The
        first level hangs off the recording's own works.
        """
        result: List[Tuple[NodeId, WorkId | None]] = []
        through: WorkId | None = None
        for node in chain:
            result.append((node, through))
            # the next level hangs off this one, reached through the work
            # the chain came down by
            through = self._child_through(node, chain)
        return result

    def _child_through(
            self,
            node: NodeId,
            chain: Sequence[NodeId]) -> WorkId | None:
        """The work of a node that the next level hangs off."""
        index = list(chain).index(node)
        if index + 1 >= len(chain):
            return None
        return self._node_child(node, chain[index + 1])

    def _node_child(self, node: NodeId, child: NodeId) -> WorkId | None:
        """The work of ``node`` that leads to ``child``.

        A node holds the works it stands for. The next level up holds
        those works' parents, so the work that links the two is the one
        of ``node`` that has a parent in ``child``.
        """
        for work in sorted(node.works, key=lambda item: item.mbid):
            if self.relations.parents.get(work, ()) and set(
                    self.relations.parents[work]) & set(child.works):
                return work
        return None

    def _level_ids(
            self,
            recording: Recording,
            node: NodeId,
            index: int,
            through: WorkId | None) -> List[WorkId]:
        """The ids one level holds, in the legacy's order.

        The first level is the recording's own works, in the order the
        performance relations came back in. The levels above it are the
        node's works, in the order the relation of the work below gave
        them.
        """
        if index == 0:
            return list(recording.members)
        return self.relations.order_of(node.works, through=through)
