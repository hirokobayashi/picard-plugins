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
from typing import (Dict, FrozenSet, Iterable, Iterator, List, Mapping, Sequence,
                    Set, Tuple)

from .album import Album, Performance, candidate_tops, chains_of
from .graph import NodeId, WorkGraph, WorkId
from .project import Hierarchy, project

#: The separator the legacy uses between the ids of one level.
ID_SEPARATOR = '; '

#: The sort key of a work that no relation named. It has to be past every
#: real key, so that such a work sorts after the ones that were named.
_UNORDERED = (1 << 30, 1 << 30)


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
            through: Iterable[WorkId] | WorkId | None = None) -> List[WorkId]:
        """The given works in the order the relation gave them.

        The legacy's node holds the parents of the works it was reached
        through, in the order those works' own relations gave them. So the
        order comes from ``through`` - the works the level was reached
        by - and not from the order the works happen to be in.

        More than one work can lead to the same level: a recording that
        performs two works puts them in one node, and that node holds
        both of their parents. Every one of those works contributes, so
        all of their parent lists are read.

        With no work given, the order is looked for in every relation.
        That is only a fallback: the result is deterministic, but the
        order it gives is the order the relations were read in, not the
        order of any one relation.

        A work that no relation names keeps its place at the end, in work
        id order, so the result is always deterministic.
        """
        wanted = list(works)
        order: Dict[WorkId, Tuple[int, int]] = {}
        if through is None:
            candidates = list(self.parents.values())
        elif isinstance(through, WorkId):
            candidates = [self.parents.get(through, ())]
        else:
            candidates = [
                self.parents.get(work, ()) for work in through]
        for source, parent_list in enumerate(candidates):
            for position, parent in enumerate(parent_list):
                if parent in wanted and parent not in order:
                    # The key is the contributing work's own index and the
                    # position within it. Two contributing works can each
                    # name a parent at position 0, and then the work that
                    # comes first has to win - not the one whose id sorts
                    # first.
                    order[parent] = (source, position)
        # A work no relation named goes after every work one did, so the
        # sentinel has to be past every real key.
        return sorted(
            wanted,
            key=lambda work: (order.get(work, _UNORDERED), work.mbid))


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

    A work related twice, both times partially, is only in the partial
    set: the recording never performs it in full.

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

    ``partial`` are the works that are only performed in part. Their
    place in the first level is taken by the recording id, so they have
    to be kept to know where they stood.
    """

    performance: Performance
    members: List[WorkId] = field(default_factory=list)
    partial: List[WorkId] = field(default_factory=list)


@dataclass
class TrackTags:
    """The hierarchy tags of one track."""

    #: The recording the tags belong to.
    key: WorkId | None = None
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
        """The tags, in the legacy's names.

        A track with no hierarchy - one whose recording names no work, or
        names a work with no place in the tree - gets no tags at all,
        which is what the legacy writes for it.
        """
        if not self.levels:
            return {}
        metadata: Dict[str, str] = {}
        for level, works in enumerate(self.levels):
            metadata['~cwp_workid_%d' % level] = self.id_string(level)
        metadata['~cwp_workid_top'] = format_ids(self.top)
        if len(self.top_names) == 1:
            metadata['~cwp_work_top'] = self.top_names[0]
        else:
            metadata['~cwp_work_top'] = ID_SEPARATOR.join(self.top_names)
        metadata['~cwp_work_group'] = self.group
        metadata['~cwp_part_levels'] = str(self.part_levels)
        metadata['~cwp_work_part_levels'] = str(self.work_part_levels)
        return metadata


def node_height(hierarchy: Hierarchy, node: NodeId) -> int:
    """The longest downward path from a node.

    This is the number of levels below the node, which is what the
    legacy's ``level_calc`` computes for the whole tree of a top work.

    The height of a node is one more than the height of its deepest
    child, so the heights are worked out from the leaves upwards. A node
    reached by two paths then gets the deeper of the two - a walk that
    stopped at the first path would miss the deeper one.

    A cycle would make the walk go on for ever, so a node already on the
    path is treated as a leaf. The hierarchy is a tree - every node has at
    most one parent - so a cycle cannot occur; the guard is there so that
    a malformed graph cannot hang the plugin.

    The walk is iterative, so a deep hierarchy cannot exhaust the stack.
    """
    heights: Dict[NodeId, int] = {}
    visiting: Set[NodeId] = set()
    done: Set[NodeId] = set()
    stack: List[Tuple[NodeId, Iterator[NodeId]]] = [
        (node, iter(sorted(_children_of(hierarchy, node))))]
    visiting.add(node)
    while stack:
        current, children = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            visiting.discard(current)
            done.add(current)
            deepest = 0
            for grandchild in _children_of(hierarchy, current):
                deepest = max(deepest, heights.get(grandchild, 0))
            heights[current] = deepest + 1
            continue
        if child in done:
            continue
        if child in visiting:
            # a cycle: the child is on the path, so it is treated as a
            # leaf. The walk carries on without it.
            continue
        visiting.add(child)
        stack.append(
            (child, iter(sorted(_children_of(hierarchy, child)))))
    return heights.get(node, 1) - 1


def _children_of(hierarchy: Hierarchy, node: NodeId) -> FrozenSet[NodeId]:
    """The children of a node, or none when the node is unknown."""
    found = hierarchy.nodes.get(node)
    return found.children if found is not None else frozenset()


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
        self.recordings = []
        seen: Dict[WorkId, Recording] = {}
        for recording in recordings:
            entry = self._read_recording(recording)
            # a release can list the same recording twice - the same track
            # on two discs, or a bonus track reusing a recording. The
            # hierarchy is built per recording, so the second one would
            # overwrite the first in the tags. The two entries are the same
            # recording, so the first is kept.
            if entry.performance.key in seen:
                continue
            seen[entry.performance.key] = entry
            self.recordings.append(entry)

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
            members=self._ordered_members(recording, performance),
            partial=sorted(performance.partial, key=lambda x: x.mbid))

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

        An id is only given once. Two works that are both performed in
        part would otherwise both be represented by the recording id, and
        the level would hold it twice - but the node holds the works as a
        set, so it can only hold the recording id once. The legacy
        de-duplicates its id list for the same reason.

        The order of the relations does not matter: a work related both
        wholly and in part is represented by the recording id either way,
        because the recording is not the work.
        """
        key = performance.key
        # the works in the order the relations came back in, and whether
        # each of them is performed in part
        order: List[WorkId] = []
        partial: Dict[WorkId, bool] = {}
        for relation in recording.get('relations') or ():
            if relation.get('type') != 'performance':
                continue
            target = relation.get('work')
            if not target or not target.get('id'):
                continue
            work = WorkId(target['id'])
            is_partial = (
                self.use_partial
                and 'partial' in (relation.get('attributes') or ()))
            if work not in partial:
                order.append(work)
                partial[work] = is_partial
            elif is_partial:
                # the work is performed in part as well, so the recording
                # id stands in for it
                partial[work] = True
        members: List[WorkId] = []
        for work in order:
            member = key if partial[work] else work
            if member not in members:
                members.append(member)
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
        """The hierarchy tags of every recording.

        A recording that names no work, or names a work with no place in
        the tree, is in the result with no levels - so a caller can tell it
        apart from a recording that is not there at all.
        """
        hierarchy, groups = self.build()
        chains = chains_of(hierarchy, self.performances)
        candidates = candidate_tops(chains, hierarchy)
        votes = self._votes(candidates)
        result: Dict[WorkId, TrackTags] = {}
        for recording in self.recordings:
            chain = chains.get(recording.performance.key) or []
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
        if best == 0:
            # none of this top's names was voted for, so there is nothing
            # to choose between
            return names[:1]
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
        levels, through = self._levels(recording, chain)
        if not levels or not levels[-1]:
            # the recording names no work, or names a work with no place
            # in the tree
            return TrackTags(key=recording.performance.key, group=group)
        top = levels[-1]
        return TrackTags(
            key=recording.performance.key,
            levels=levels,
            top=top,
            top_names=self._top_names(top, votes),
            group=group,
            part_levels=len(chain) - 1,
            work_part_levels=node_height(hierarchy, chain[-1]))

    def _levels(
            self,
            recording: Recording,
            chain: Sequence[NodeId]) -> Tuple[List[List[WorkId]],
                                              List[List[WorkId]]]:
        """The ids of each level, and the works each level was reached by.

        The levels are worked out from the bottom up, because a level's
        order comes from the relations of the works below it - so the
        works that lead to a level are read in the order the level below
        gave them.
        """
        levels: List[List[WorkId]] = []
        through: List[List[WorkId]] = []
        for index, node in enumerate(chain):
            if index == 0:
                ids = list(recording.members)
                leaders: List[WorkId] = []
            else:
                leaders = self._works_leading_to(
                    chain[index - 1], node, levels[index - 1],
                    recording.performance.key, recording.partial)
                ids = self.relations.order_of(node.works, through=leaders)
            levels.append(ids)
            through.append(leaders)
        return levels, through

    def _works_leading_to(
            self,
            node: NodeId,
            child: NodeId,
            below: Sequence[WorkId],
            recording: WorkId,
            partial: Sequence[WorkId]) -> List[WorkId]:
        """The works of ``node`` whose parents are in ``child``.

        A node holds the works it stands for. The next level up holds
        those works' parents, so every work of the node that has a
        parent in the child contributes to it. A recording that performs
        two works puts them in one node, and then both of them
        contribute - picking only one of them would lose the other's
        contribution to the order.

        The works are in the order the level below gave them, not in work
        id order: the level's order comes from the relations, and the id
        order would decide it instead.

        A partly performed work is represented by the recording id in
        the level below, so the level below cannot say where it stood.
        It takes the recording id's place, which is where the relation
        put it.
        """
        leading = [
            work for work in node.works
            if self.relations.parents.get(work, ())
            and set(self.relations.parents[work]) & set(child.works)]
        order = {work: position for position, work in enumerate(below)}
        if recording in order:
            # the recording id stands in for the partly performed works,
            # so they take its place
            for work in leading:
                if work not in order and work in partial:
                    order[work] = order[recording]
        return sorted(
            leading,
            key=lambda work: (order.get(work, len(order)), work.mbid))
