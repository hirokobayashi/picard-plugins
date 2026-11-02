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
* a work that is related twice is decided by its **first** relation: if
  that one is partial, the work is represented by the recording id
  alone, because the recording is not the work; if that one is in full,
  the work keeps its own id. See ``read_performance``;
* the 'partial' substitution only applies when the plugin's
  ``cwp_partial`` option is on.

The levels above the first are the node's works in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Dict, FrozenSet, Iterable, Iterator, List, Mapping, Optional,
                    Sequence, Set, Tuple)

from .album import Album, Performance, candidate_tops, chains_of
from .graph import NodeId, WorkGraph, WorkId
from .project import Hierarchy, project

#: The sort key of a work that no relation named. It has to be past every
#: real key, so that such a work sorts after the ones that were named.
_UNORDERED = (1 << 30, 1 << 30)


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
        That is only a fallback: the result is deterministic - the
        relations are read in work id order - but the order it gives is
        the order the relations were read in, not the order of any one
        relation.

        An empty list of works is not the same as no work given: there
        the caller knows which work the level was reached by and it
        names none, so the works are left in work id order rather than
        given an order from an unrelated relation.

        A work that no relation names keeps its place at the end, in work
        id order, so the result is always deterministic.
        """
        wanted = list(works)
        order: Dict[WorkId, Tuple[int, int]] = {}
        if through is None:
            # no work to read the order from, so every relation is read.
            # The relations are read in work id order, so the result does
            # not depend on the order the works happened to be found in.
            candidates = [
                self.parents[work]
                for work in sorted(self.parents, key=lambda x: x.mbid)]
        elif isinstance(through, WorkId):
            candidates = [self.parents.get(through, ())]
        elif isinstance(through, str):
            # a bare work id
            candidates = [self.parents.get(WorkId(through), ())]
        else:
            # the list is taken once, so that it can be read to build the
            # candidates. A set would give a run-to-run order, so it is
            # put in work id order first.
            sources = [
                work if isinstance(work, WorkId) else WorkId(work)
                for work in through]
            if not isinstance(through, (list, tuple)):
                sources.sort(key=lambda work: work.mbid)
            candidates = [
                self.parents.get(work, ()) for work in sources]
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
    a parent edge, and an ``arrangement`` or a ``medley`` relation is a
    parent edge too when there is no ``parts`` relation - the graph's
    own ordered fallback. Every other relation is retained but takes no
    part in the hierarchy.

    The order the parents came back in is returned as well, because the
    legacy's node keys are tuples and the order inside a tuple is the
    order the relations came back in. The parents are the *resolved*
    ones - the graph's own ordered fallback of ``parts``,
    ``arrangement`` and ``medley`` - because that is the set the
    hierarchy is built from.
    """
    graph = WorkGraph()
    for response in works:
        if (not isinstance(response, Mapping)
                or not response.get('id')):
            # a response that is not an object, or has no id, names no
            # work - a failed lookup, or an empty body. It cannot be
            # placed in the graph, but it must not stop the rest of the
            # release from being tagged.
            continue
        source = WorkId(response['id'])
        graph.add_work(source)
        for relation in response.get('relations') or ():
            if not isinstance(relation, Mapping):
                # a relation that is not an object names nothing
                continue
            target = relation.get('work')
            if not isinstance(target, Mapping) or not target.get('id'):
                continue
            graph.add_relation(
                source,
                relation.get('type'),
                relation.get('direction'),
                WorkId(target['id']),
                _attributes_of(relation))
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

    The *first* relation for a work decides whether the recording stands
    in for it: the legacy reads the attributes of the first relation it
    finds for the work. So a work related partly first and in full
    second is in the partial set and the complete set, and a work
    related in full first and partly second is only in the complete
    set.

    A work related twice, both times partially, is only in the partial
    set: the recording never performs it in full.

    ``use_partial`` is the plugin's ``cwp_partial`` option: with it off
    the attribute is ignored and a partially performed work is an ordinary
    one.

    A response with no id names no recording, and this raises - the
    caller has to filter those out. ``AlbumAdapter`` does, so a release
    with a track that has no recording attached is tagged as usual.

    The relation's direction is not looked at, because the legacy does
    not look at it either: it matches the recording's relations by the
    work they name. A performance relation is forward in MusicBrainz's
    data, so there is nothing to tell apart in practice.
    """
    if not recording.get('id'):
        # a response with no id names no recording
        raise ValueError("the recording has no id")
    key = WorkId(recording['id'])
    works: List[WorkId] = []
    partial: List[WorkId] = []
    complete: List[WorkId] = []
    for relation in recording.get('relations') or ():
        if not isinstance(relation, Mapping):
            # a relation that is not an object names nothing
            continue
        if relation.get('type') != 'performance':
            continue
        target = relation.get('work')
        if not isinstance(target, Mapping) or not target.get('id'):
            continue
        work = WorkId(target['id'])
        if work in works:
            # The work is related again, but the first relation decides:
            # the legacy reads the attributes of the first relation it
            # finds for the work.
            #
            # A work related partly first and in full second is in both
            # sets - the legacy adds a second entry for it, so the work
            # itself still contributes. A work related partly twice is
            # only in the partial set: the recording never performs it
            # in full.
            if (work in partial and use_partial
                    and work not in complete
                    and 'partial' not in (
                        _attributes_of(relation))):
                complete.append(work)
            continue
        works.append(work)
        if use_partial and 'partial' in _attributes_of(relation):
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

    ``partial`` are the works the recording performs in part - including
    a work it performs in part *and* in full. Their place in the first
    level is taken by the recording id, so they have to be kept to know
    where they stood.

    ``performed`` are the works the recording performs, in the order the
    relations gave them.

    ``raw`` is the recording's own data, kept so that two entries for
    the same recording can be compared.
    """

    performance: Performance
    members: List[WorkId] = field(default_factory=list)
    partial: List[WorkId] = field(default_factory=list)
    performed: List[WorkId] = field(default_factory=list)
    raw: Mapping = field(default_factory=dict)


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
    #: The group name - the shuffle key, always one value. A track with
    #: no hierarchy has a group too, but no tags are written for it.
    group: str = ''
    #: The number of levels below this track's own level.
    part_levels: int = 0
    #: The number of levels in the whole hierarchy of this track's top.
    work_part_levels: int = 0

    @staticmethod
    def id_tuple(works: Sequence[WorkId]) -> Tuple[str, ...]:
        """The work ids of a level, as the tuple the legacy hands Picard.

        Picard stores a tuple of one id as a bare string and a tuple of
        more as a list, so this is what the legacy's own tags read as.
        """
        return tuple(work.mbid for work in works)

    def as_metadata(self) -> Dict[str, object]:
        """The tags, in the legacy's names.

        A track with no hierarchy - one whose recording names no work, or
        names a work with no place in the tree - gets no tags at all,
        which is what the legacy writes for it.

        The work id tags hold a tuple of ids, as the legacy's do. Picard
        stores a tuple as a bare string when it holds one id and as a
        list when it holds more, so this is what the legacy's own tags
        read as - a single id is bare, and two are separated by a
        semicolon.
        """
        if not self.levels or not any(self.levels):
            # a track with no hierarchy gets no tags at all
            return {}
        metadata: Dict[str, object] = {}
        # a level with no ids has no tag - the legacy writes a tag only
        # when it has a value. The levels are numbered by the ones that
        # do have ids, so the numbering has no gaps.
        number = 0
        for works in self.levels:
            if not works:
                continue
            metadata['~cwp_workid_%d' % number] = self.id_tuple(works)
            number += 1
        if self.top:
            metadata['~cwp_workid_top'] = tuple(
                work.mbid for work in self.top)
        if self.top_names:
            if len(self.top_names) == 1:
                metadata['~cwp_work_top'] = self.top_names[0]
            else:
                # a tie keeps every tied name, and the legacy hands
                # Picard the list of them - so Picard stores a list
                metadata['~cwp_work_top'] = tuple(self.top_names)
        if self.group:
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
    path is left out: it is treated as absent, so the node above it
    counts it as nothing. The hierarchy is a tree - every node has at
    most one parent - so a cycle cannot occur; the guard is there so that
    a malformed graph cannot hang the plugin, and the height it gives in
    that case is not to be relied on.

    The walk is iterative, so a deep hierarchy cannot exhaust the stack.

    Use ``heights_of`` when the heights of several nodes of the same
    hierarchy are wanted: it works them out once.

    A node that the hierarchy does not hold raises - a caller that
    passes a stale or mistyped node would otherwise get a height of
    zero, which is the height of a leaf.
    """
    heights = heights_of(hierarchy, node)
    if node not in heights:
        raise KeyError("the hierarchy has no node %s" % (node,))
    return heights[node]


def heights_of(
        hierarchy: Hierarchy,
        node: NodeId) -> Dict[NodeId, int]:
    """The height of a node and of every node below it.

    The height of a node is the number of levels below it - so a leaf
    has the height zero.

    The heights of a tree are worked out once, so that the tracks that
    share a top do not each walk it again.

    The heights are of a well-formed hierarchy: a child that the
    hierarchy does not hold contributes nothing, so the node above it
    counts one level rather than the depth of the subtree that is not
    there. The hierarchy is built by the model, so it is well formed.

    A cycle in the hierarchy is left out of the walk, so the heights
    that come back are not to be relied on. ``has_cycle`` says when
    that happened.
    """
    heights, _cycle = _heights_of(hierarchy, node)
    return heights


def has_cycle(hierarchy: Hierarchy, node: NodeId) -> bool:
    """Whether the hierarchy has a cycle on the way down from the node.

    The hierarchy is a tree, so a cycle cannot occur - but a malformed
    graph could have one, and the heights of a walk that found one are
    not to be relied on.
    """
    _heights, cycle = _heights_of(hierarchy, node)
    return cycle


def _heights_of(
        hierarchy: Hierarchy,
        node: NodeId) -> Tuple[Dict[NodeId, int], bool]:
    """The heights of a walk, and whether it found a cycle.

    A child the hierarchy does not hold is counted as a leaf, so the
    depth it gives is too small. The walk does not report that - the
    hierarchy is built by the model, so it is well formed.
    """
    heights: Dict[NodeId, int] = {}
    visiting: Set[NodeId] = set()
    done: Set[NodeId] = set()
    cycle = False
    stack: List[Tuple[NodeId, Iterator[NodeId]]] = [
        (node, iter(_children_of(hierarchy, node)))]
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
            # a node the hierarchy does not hold has no height - it is
            # not recorded, so a caller can tell it from a leaf
            if current in hierarchy.nodes:
                heights[current] = deepest + 1
            continue
        if child in done:
            continue
        if child in visiting:
            # a cycle: the child is on the path, so it is left out. The
            # walk carries on without it - but the heights it gives are
            # not to be relied on, so the caller is told.
            cycle = True
            continue
        visiting.add(child)
        stack.append((child, iter(_children_of(hierarchy, child))))
    # the walk counts a node itself, so the heights are one too many
    return {
        node_id: height - 1 for node_id, height in heights.items()}, cycle


def _attributes_of(relation: Mapping) -> Sequence[str]:
    """The attributes of a relation.

    The attributes are a list of names. A relation whose attributes are
    something else - a string, say - has none: a string would be
    searched as a list of characters, and ``'partial' in 'partially'``
    is true.
    """
    attributes = relation.get('attributes')
    if isinstance(attributes, (list, tuple)):
        return [str(item) for item in attributes]
    return ()


def _children_of(hierarchy: Hierarchy, node: NodeId) -> FrozenSet[NodeId]:
    """The children of a node, or none when the node is unknown."""
    found = hierarchy.nodes.get(node)
    return found.children if found is not None else frozenset()


def _has_missing_child(hierarchy: Hierarchy, node: NodeId) -> bool:
    """Whether the hierarchy has a child the hierarchy does not hold.

    Such a child is counted as a leaf, so the depth of the tree under it
    is lost and the depth that comes back is too small. The hierarchy
    is built by the model, so it is well formed - but a malformed one
    would give a plausible-looking wrong depth.
    """
    seen: Set[NodeId] = set()
    pending = [node]
    while pending:
        current = pending.pop()
        for child in _children_of(hierarchy, current):
            if child not in hierarchy.nodes:
                return True
            if child in seen:
                continue
            seen.add(child)
            pending.append(child)
    return False


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
            use_collections: bool = True,
            multiplicity: Mapping[WorkId, int] | None = None) -> None:
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
        #: How many tracks each recording is on. The legacy votes once
        #: per *track*, so a recording on two tracks votes twice.
        #:
        #: The keys are the recording ids. A plain string is taken as an
        #: id, so that a caller that has the ids as strings is not
        #: silently ignored.
        self.multiplicity: Dict[WorkId, int] = {
            (key if isinstance(key, WorkId) else WorkId(key)): count
            for key, count in (multiplicity or {}).items()}
        self.recordings = []
        #: The recordings that were listed twice and dropped, so that a
        #: caller can see that it happened.
        self.dropped: List[WorkId] = []
        #: The recordings that were listed twice with *different*
        #: performances. The first entry's tags win - the tags are per
        #: recording, so there is nowhere for both to go - but the caller
        #: can see that the two disagreed, which is a different thing
        #: from a repeated recording.
        #:
        #: Only the performance relations are compared: they are the
        #: ones the tags are worked out from. Two entries that differ
        #: only in other relations are the same recording as far as the
        #: tags are concerned.
        #:
        #: The comparison is made with the option as it is set, so two
        #: entries that differ only in the partial attribute are the
        #: same recording when the option is off - and the tags would
        #: be the same then too.
        self.conflicting: List[WorkId] = []
        #: The candidate tops of the hierarchy, worked out when it is
        #: built and kept so that the tags do not walk the chains again.
        self._candidates: Dict[WorkId, List[WorkId]] = {}
        #: The built hierarchy, kept so that it is not built twice.
        self._built: Optional[Tuple[
            Hierarchy, Dict[WorkId, List[NodeId]], Dict[WorkId, str]]] = None
        seen: Dict[WorkId, Recording] = {}
        for recording in recordings:
            if (not isinstance(recording, Mapping)
                    or not recording.get('id')):
                # a response that is not an object, or has no id, names
                # no recording - a track with no recording attached, or a
                # failed lookup. It cannot be tagged, but it must not
                # stop the rest of the release from being tagged.
                continue
            entry = self._read_recording(recording)
            # a release can list the same recording twice - the same track
            # on two discs, or a bonus track reusing a recording. The
            # hierarchy is built per recording, so the second one would
            # overwrite the first in the tags. The two entries are the same
            # recording - the same performance of the same works - so the
            # first is kept. If they were not the same, the second's
            # relations would be dropped: the tags are per recording, so
            # there is nowhere for both to go. The dropped one is
            # recorded, so that a caller can see it happened.
            if entry.performance.key in seen:
                self.dropped.append(entry.performance.key)
                if self._performance_of(
                        recording) != self._performance_of(
                            seen[entry.performance.key].raw):
                    self.conflicting.append(entry.performance.key)
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
            members=self._ordered_members(recording, performance.key),
            partial=self._partial_works(recording),
            performed=self._performed_works(recording),
            raw=recording)

    def _performance_of(
            self,
            recording: Mapping) -> Tuple[Tuple[WorkId, ...], ...]:
        """The recording's performance, in full.

        The works in the order the relations gave them, and whether each
        is in part. A work related more than once is held once - the
        first relation decides - but the relations after it still count
        for the model's performance, so the whole performance is
        compared rather than just the first relations.
        """
        performance = read_performance(
            recording, use_partial=self.use_partial)
        return (
            # the order the relations gave, which decides the order of
            # the first level
            tuple(self._performed_works(recording)),
            tuple(sorted(performance.works, key=lambda work: work.mbid)),
            tuple(sorted(performance.partial, key=lambda work: work.mbid)),
            tuple(sorted(performance.complete, key=lambda work: work.mbid)),
        )

    def _partial_works(
            self,
            recording: Mapping) -> List[WorkId]:
        """The partly performed works, in the order the relations gave them.

        The order matters: a partly performed work is represented by the
        recording id in the first level, so the first level cannot say
        where it stood, and this list is what says instead.
        """
        return [
            work for work, partial in self._performed(recording)
            if partial]

    def _performed_works(
            self,
            recording: Mapping) -> List[WorkId]:
        """The works the recording performs, in the relation order."""
        return [work for work, _partial in self._performed(recording)]

    def _performed(
            self,
            recording: Mapping) -> List[Tuple[WorkId, bool]]:
        """The works the recording performs, and whether each is in part.

        The first level's ids and the partly performed works come from
        this one pass, so they cannot drift apart. The model's own
        performance is read separately by ``read_performance``; the two
        agree, and a test checks that they do.

        A work that is related twice is decided by the *first* of the
        relations: the legacy reads the attributes of the first
        relation it finds for the work, so a work related in full first
        and partly second is a work performed in full - the recording id
        does not stand in for it. A work related partly first is a work
        performed in part, whatever the second relation says.
        """
        result: List[Tuple[WorkId, bool]] = []
        seen: Set[WorkId] = set()
        for relation in recording.get('relations') or ():
            if not isinstance(relation, Mapping):
                # a relation that is not an object names nothing
                continue
            if relation.get('type') != 'performance':
                continue
            target = relation.get('work')
            if not isinstance(target, Mapping) or not target.get('id'):
                continue
            work = WorkId(target['id'])
            if work in seen:
                # The first relation for the work decides - the legacy
                # reads the attributes of the first relation it finds
                # for the work. So the recording id stands in for the
                # work when the *first* relation is a partial one, and
                # does not when the first is a full one.
                #
                # A work related partly first is still performed in full
                # as well - the legacy adds a second entry for it - but
                # that is the model's own business, and the model's
                # performance says so.
                continue
            is_partial = (
                self.use_partial
                and 'partial' in _attributes_of(relation))
            seen.add(work)
            result.append((work, is_partial))
        return result

    def _ordered_members(
            self,
            recording: Mapping,
            key: WorkId) -> List[WorkId]:
        """The recording's first level ids, in the legacy's order.

        The performance relations in the order they came back in, with a
        partially performed work represented by the recording id in the
        relation's place. A work related partly first - and then in full
        - is represented by the recording id alone, because the
        recording is not the work.

        Which id stands for a work is decided by the *first* relation
        for it: the legacy reads the attributes of the first relation it
        finds for the work, so a work related wholly first is the work
        itself and the recording id does not stand in for it. Where the
        recording id stands does depend on the order of the relations:
        it takes the place of the first relation to the partly
        performed work, so the level reads the same as the relations
        did.

        An id is only given once. Two works that are both performed in
        part would otherwise both be represented by the recording id, and
        the level would hold it twice - but the node holds the works as a
        set, so it can only hold the recording id once. The legacy
        de-duplicates its id list for the same reason.
        """
        members: List[WorkId] = []
        for work, partial in self._performed(recording):
            member = key if partial else work
            if member not in members:
                members.append(member)
        return members

    def build(self) -> Tuple[Hierarchy, Dict[WorkId, str]]:
        """The hierarchy and the group of every recording."""
        hierarchy, _chains, groups = self.build_all()
        return hierarchy, groups

    def build_all(self) -> Tuple[Hierarchy, Dict[WorkId, List[NodeId]],
                                Dict[WorkId, str]]:
        """The hierarchy, the chain of every recording, and the groups.

        The hierarchy is built once and kept, so that a caller that
        asks for the build and then for the tags does not walk the
        whole graph twice.
        """
        if self._built is not None:
            return self._built
        hierarchy = project(
            self.graph,
            use_arrangements=self.use_arrangements,
            use_medleys=self.use_medleys,
            use_collections=self.use_collections,
            performances=self.performances)
        chains = chains_of(hierarchy, self.performances)
        album = Album(names=self.names.titles)
        candidates = candidate_tops(chains, hierarchy)
        groups = album.group_names(candidates)
        # the candidates are kept, so that the tags do not have to walk
        # the chains a second time
        self._candidates = candidates
        self._built = (hierarchy, chains, groups)
        return self._built

    def tags(self) -> Dict[WorkId, TrackTags]:
        """The hierarchy tags of every recording.

        A recording that names no work, or names a work with no place in
        the tree, is in the result with no levels - so a caller can tell it
        apart from a recording that is not there at all.

        The result is keyed by the recording id, so a caller looks the
        tags up by the recording of the track it is tagging. A release
        that lists the same recording on two tracks gets the same tags
        for both - the tags are the recording's, not the track's.

        A recording that was listed twice with different relations keeps
        the first one's tags; the ones that were dropped are in
        ``dropped``.
        """
        hierarchy, chains, groups = self.build_all()
        votes = self._votes(self._candidates)
        # the height of a top is the same for every track that shares it,
        # so it is worked out once per top
        heights: Dict[NodeId, Dict[NodeId, int]] = {}
        cycles: Dict[NodeId, bool] = {}
        result: Dict[WorkId, TrackTags] = {}
        for recording in self.recordings:
            chain = chains.get(recording.performance.key) or []
            result[recording.performance.key] = self._tags_for(
                recording, chain, hierarchy,
                groups.get(recording.performance.key, ''),
                votes, heights, cycles)
        return result

    def _votes(
            self,
            candidates: Mapping[WorkId, FrozenSet[WorkId]]) -> Dict[str, int]:
        """How many tracks of the album have each name as a candidate top.

        The legacy votes over the *names* of a track's candidate top
        works, and the winner becomes the track's top work tag.

        The vote is per *track*, so a recording that is on two tracks
        votes twice - which the multiplicity says.
        """
        votes: Dict[str, int] = {}
        for key, works in candidates.items():
            count = self.multiplicity.get(key, 1)
            for name in set(self.names.names_of(works)):
                votes[name] = votes.get(name, 0) + count
        return votes

    def _top_names(
            self,
            top: Sequence[WorkId],
            votes: Mapping[str, int]) -> List[str]:
        """The names of a track's top work.

        The legacy picks the candidate name the most tracks of the album
        voted for, so that every track sharing a top names it the same
        way. A tie keeps every tied name.

        With no votes at all the legacy falls back to the first name.
        With votes that name none of the candidates, every name is tied
        at zero - so they are all kept, as the legacy keeps them.
        """
        names = self.names.names_of(top)
        if not names:
            return []
        if not votes:
            return names[:1]
        best = max(votes.get(name, 0) for name in names)
        winners = [name for name in names if votes.get(name, 0) == best]
        if len(winners) == 1:
            return winners
        # a tie keeps every tied name, in a fixed order - not the order of
        # this track's top list, which differs between the tracks that
        # share a top
        return sorted(winners)

    def _tags_for(
            self,
            recording: Recording,
            chain: Sequence[NodeId],
            hierarchy: Hierarchy,
            group: str,
            votes: Mapping[str, int],
            heights: Dict[NodeId, Dict[NodeId, int]],
            cycles: Dict[NodeId, bool]) -> TrackTags:
        """The tags of one recording, from its chain.

        ``~cwp_part_levels`` is the number of levels between the track and
        the top - how deep the track's own work sits in the hierarchy.
        ``~cwp_work_part_levels`` is the depth of the whole hierarchy
        under this track's top, which is the same for every track that
        shares the top - so it is worked out once per top and kept in
        ``heights``.
        """
        levels, _through = self._levels(recording, chain)
        # the top is the last level that has ids - a level with no ids
        # has no tag, so it is not a level of the track's own. The node
        # of that level is the one the depth comes from, so that the
        # two cannot come from different nodes.
        filled = [(index, works)
                  for index, works in enumerate(levels) if works]
        if not filled:
            # the recording names no work, or names a work with no place
            # in the tree
            return TrackTags(key=recording.performance.key, group=group)
        top_index, top = filled[-1]
        tip = chain[top_index]
        if tip not in heights:
            # the walk gives the heights and whether it found a cycle,
            # so it is only walked once
            heights[tip], cycles[tip] = _heights_of(hierarchy, tip)
            # a child the hierarchy does not hold is counted as a leaf,
            # so the depth would be too small - the track is left with
            # no tags rather than a wrong depth
            cycles[tip] = cycles[tip] or _has_missing_child(
                hierarchy, tip)
        # A chain whose tip the hierarchy does not hold has no depth to
        # report - and zero is the depth of a one-level hierarchy, so a
        # plausible-looking wrong value would be written. The track is
        # left with no tags instead: one bad chain must not take the
        # rest of the release with it.
        work_part_levels = heights[tip].get(tip)
        if work_part_levels is None:
            return TrackTags(key=recording.performance.key, group=group)
        # A cycle in the hierarchy makes the depth unreliable, so the
        # track is left with no tags rather than a wrong depth.
        if cycles[tip]:
            return TrackTags(key=recording.performance.key, group=group)
        return TrackTags(
            key=recording.performance.key,
            levels=levels,
            top=top,
            top_names=self._top_names(top, votes),
            group=group,
            # the number of levels below the track's own - the same
            # count as the tags are numbered by, so the two agree
            part_levels=len(filled) - 1,
            work_part_levels=work_part_levels)

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
                    chain[index - 1], node, levels[index - 1])
                ids = self.relations.order_of(node.works, through=leaders)
                if index == 1:
                    # The level above the first can hold the partly
                    # performed works - the recording id stands in for
                    # them below, so they are not named by the level
                    # below and their order cannot come from it.
                    #
                    # They take the recording id's place in the level
                    # below, which is where the relations put them. So
                    # the level is built by walking the works that lead
                    # to it in the order the level below gave them, and
                    # taking each one's contribution: its parents, or
                    # the work itself when it is partly performed.
                    ids = self._level_above_first(
                        leaders, levels[0], recording, ids)
            levels.append(ids)
            through.append(leaders)
        return levels, through

    def _level_above_first(
            self,
            leaders: Sequence[WorkId],
            below: Sequence[WorkId],
            recording: Recording,
            ordered: Sequence[WorkId]) -> List[WorkId]:
        """The level above the first one, in the order the relations gave.

        The level holds the works that lead to it - the parents of the
        works below - and a partly performed work itself, because the
        recording id stands in for it in the level below and so cannot
        say where it stood.

        The order is the order the works below gave, with each work
        contributing in turn: a partly performed work contributes
        itself, and any other work contributes its parents. So the
        partly performed work takes the recording id's place, and the
        works before it keep theirs.

        A partly performed work can be among the works that lead to a
        level - a work performed only in part contributes nothing, but a
        work performed both in part and in full contributes itself, and
        so it sits in the level above and leads to the one above that.

        This is about the level *directly* above the first, and this
        method is only used for that level. A level higher up holds the
        parents of the works below it, so a partly performed work there
        contributes its parents as any other work does.

        The works that lead to the level are in the order the level
        below gave them, which is the order the relations gave.
        """
        partial = set(recording.partial)
        result: List[WorkId] = []
        seen: Set[WorkId] = set()
        # the works that lead to the level, and the partly performed
        # works - which are not among them, because the recording id
        # stands in for them in the level below. A partly performed work
        # takes the recording id's place there, so that is where it
        # goes here too.
        place = {
            work: position
            for position, work in enumerate(below)}
        key = recording.performance.key
        # the partly performed works that are not among the leaders take
        # the recording id's place - and keep their own order among
        # themselves, which is the order the relations gave them
        partial_order = {
            work: position
            for position, work in enumerate(recording.partial)}
        contributors = list(leaders) + [
            work for work in recording.partial
            if work in ordered and work not in leaders]
        # A partly performed work takes the recording id's place in the
        # level below. A work that leads to the level keeps its own
        # place there - and a work the level below does not name goes
        # last, as it has no place of its own.
        contributors.sort(key=lambda work: (
            place.get(
                work,
                place.get(key, len(below))
                if work in partial else len(below)),
            partial_order.get(work, 0),
            work.mbid))
        for work in contributors:
            if work in partial:
                # the work contributes itself, but only if the level
                # holds it
                contribution: List[WorkId] = (
                    [work] if work in ordered else [])
            else:
                contribution = [
                    parent for parent in self.relations.parents.get(work, ())
                    if parent in ordered]
            for item in contribution:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
        # a work of the level that no leading work contributed keeps the
        # place the relations gave it
        for work in ordered:
            if work not in seen:
                seen.add(work)
                result.append(work)
        return result

    def _works_leading_to(
            self,
            below_node: NodeId,
            above_node: NodeId,
            below: Sequence[WorkId]) -> List[WorkId]:
        """The works of ``below_node`` whose parents are in ``above_node``.

        A node holds the works it stands for. The next level up holds
        those works' parents, so every work of the node that has a
        parent in the node above contributes to it. A recording that
        performs two works puts them in one node, and then both of them
        contribute - picking only one of them would lose the other's
        contribution to the order.

        The works are in the order the level below gave them, not in work
        id order: the level's order comes from the relations, and the id
        order would decide it instead.

        A partly performed work is not among them - the recording id
        stands in for it in the level below - so its place in the level
        above is worked out by ``_level_above_first``.
        """
        leading = [
            work for work in below_node.works
            if self.relations.parents.get(work, ())
            and set(self.relations.parents[work]) & set(above_node.works)]
        # the key is the place in the level below - a work the level
        # below does not name goes last
        order: Dict[WorkId, Tuple[int, int]] = {
            work: (position, 0)
            for position, work in enumerate(below)}
        return sorted(
            leading,
            key=lambda work: (
                order.get(work, (len(below), 0)),
                work.mbid))
