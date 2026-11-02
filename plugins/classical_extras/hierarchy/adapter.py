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
            sources = list(through)
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

    The order of the relations does not matter: any partly performed
    relation puts the work in the partial set, and any relation in full
    puts it in the complete set.

    A work related twice, both times partially, is only in the partial
    set: the recording never performs it in full.

    ``use_partial`` is the plugin's ``cwp_partial`` option: with it off
    the attribute is ignored and a partially performed work is an ordinary
    one.

    The relation's direction is not looked at, because the legacy does
    not look at it either: it matches the recording's relations by the
    work they name. A performance relation is forward in MusicBrainz's
    data, so there is nothing to tell apart in practice.
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
            # work that is not partial makes it performed in full as
            # well
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

    def id_tuple(self, level: int) -> Tuple[str, ...]:
        """The work ids of one level, as the tuple the legacy hands Picard.

        Picard stores a tuple of one id as a bare string and a tuple of
        more as a list, so this is what the legacy's own tags read as.
        """
        return tuple(work.mbid for work in self.levels[level])

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
        if not self.levels:
            return {}
        metadata: Dict[str, object] = {}
        for level, works in enumerate(self.levels):
            if not works:
                # a level with no ids has no tag - the legacy writes a tag
                # only when it has a value
                continue
            metadata['~cwp_workid_%d' % level] = self.id_tuple(level)
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
    path is treated as a leaf. The hierarchy is a tree - every node has at
    most one parent - so a cycle cannot occur; the guard is there so that
    a malformed graph cannot hang the plugin.

    The walk is iterative, so a deep hierarchy cannot exhaust the stack.

    Use ``heights_of`` when the heights of several nodes of the same
    hierarchy are wanted: it works them out once.
    """
    return heights_of(hierarchy, node).get(node, 0)


def heights_of(
        hierarchy: Hierarchy,
        node: NodeId) -> Dict[NodeId, int]:
    """The height of a node and of every node below it.

    The height of a node is the number of levels below it - so a leaf
    has the height zero.

    The heights of a tree are worked out once, so that the tracks that
    share a top do not each walk it again.
    """
    heights: Dict[NodeId, int] = {}
    visiting: Set[NodeId] = set()
    done: Set[NodeId] = set()
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
            heights[current] = deepest + 1
            continue
        if child in done:
            continue
        if child in visiting:
            # a cycle: the child is on the path, so it is treated as a
            # leaf. The walk carries on without it.
            continue
        visiting.add(child)
        stack.append((child, iter(_children_of(hierarchy, child))))
    # the walk counts a node itself, so the heights are one too many
    return {node_id: height - 1 for node_id, height in heights.items()}


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
        #: The recordings that were listed twice and dropped, so that a
        #: caller can see that it happened.
        self.dropped: List[WorkId] = []
        #: The recordings that were listed twice with *different* relations.
        #: The first entry's tags win - the tags are per recording, so
        #: there is nowhere for both to go - but the caller can see that
        #: the two disagreed, which is a different thing from a repeated
        #: recording.
        self.conflicting: List[WorkId] = []
        seen: Dict[WorkId, Recording] = {}
        for recording in recordings:
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
                if self._performed(recording) != self._performed(
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
        """
        result: List[Tuple[WorkId, bool]] = []
        seen: Set[WorkId] = set()
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
            if work in seen:
                if is_partial:
                    # the work is performed in part as well, so the
                    # recording id stands in for it
                    result = [
                        (held, True) if held == work else (held, part)
                        for held, part in result]
                continue
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
        relation's place. A work related twice - once partially, once in
        full - is represented by the recording id alone, because the
        recording is not the work.

        Which id stands for a work does not depend on the order of the
        relations, but where the recording id stands does: it takes the
        place of the first relation to the partly performed work, so the
        level reads the same as the relations did.

        An id is only given once. Two works that are both performed in
        part would otherwise both be represented by the recording id, and
        the level would hold it twice - but the node holds the works as a
        set, so it can only hold the recording id once. The legacy
        de-duplicates its id list for the same reason.

        The order of the relations does not matter for which id stands
        for a work: a work related both wholly and in part is
        represented by the recording id either way, because the
        recording is not the work. It does matter for where the
        recording id stands.
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
        """The hierarchy, the chain of every recording, and the groups."""
        hierarchy = project(
            self.graph,
            use_arrangements=self.use_arrangements,
            use_medleys=self.use_medleys,
            use_collections=self.use_collections,
            performances=self.performances)
        chains = chains_of(hierarchy, self.performances)
        album = Album(names=self.names.titles)
        groups = album.group_names(candidate_tops(chains, hierarchy))
        return hierarchy, chains, groups

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
        candidates = candidate_tops(chains, hierarchy)
        votes = self._votes(candidates)
        # the height of a top is the same for every track that shares it,
        # so it is worked out once per top
        heights: Dict[NodeId, Dict[NodeId, int]] = {}
        result: Dict[WorkId, TrackTags] = {}
        for recording in self.recordings:
            chain = chains.get(recording.performance.key) or []
            result[recording.performance.key] = self._tags_for(
                recording, chain, hierarchy,
                groups.get(recording.performance.key, ''),
                votes, heights)
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
            heights: Dict[NodeId, Dict[NodeId, int]]) -> TrackTags:
        """The tags of one recording, from its chain.

        ``~cwp_part_levels`` is the number of levels between the track and
        the top - how deep the track's own work sits in the hierarchy.
        ``~cwp_work_part_levels`` is the depth of the whole hierarchy
        under this track's top, which is the same for every track that
        shares the top - so it is worked out once per top and kept in
        ``heights``.
        """
        levels, _through = self._levels(recording, chain)
        if not levels or not levels[-1]:
            # the recording names no work, or names a work with no place
            # in the tree
            return TrackTags(key=recording.performance.key, group=group)
        top = levels[-1]
        if chain[-1] not in heights:
            heights[chain[-1]] = heights_of(hierarchy, chain[-1])
        return TrackTags(
            key=recording.performance.key,
            levels=levels,
            top=top,
            top_names=self._top_names(top, votes),
            group=group,
            part_levels=len(chain) - 1,
            work_part_levels=heights[chain[-1]][chain[-1]])

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
                if index == 1:
                    # The level above the first can hold the partly
                    # performed works - the recording id stands in for
                    # them below, so they are not named by the level
                    # below and their order cannot come from it.
                    #
                    # They take the recording id's place in the level
                    # below, which is where the relations put them, and
                    # the works the level below does name keep their own
                    # place. So the two are put back together in the
                    # order the relations gave.
                    partial = [
                        work for work in recording.partial
                        if work in ids]
                    if partial:
                        place = levels[0].index(recording.performance.key)
                        others = [
                            work for work in ids if work not in partial]
                        ids = (
                            others[:place] + partial
                            + others[place:])
            levels.append(ids)
            through.append(leaders)
        return levels, through

    def _works_leading_to(
            self,
            below_node: NodeId,
            above_node: NodeId,
            below: Sequence[WorkId],
            recording: WorkId,
            partial: Sequence[WorkId]) -> List[WorkId]:
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

        A partly performed work is represented by the recording id in
        the level below, so the level below cannot say where it stood.
        It takes the recording id's place, which is where the relation
        put it - the level below names the recording id, so the work is
        found there.
        """
        leading = [
            work for work in below_node.works
            if self.relations.parents.get(work, ())
            and set(self.relations.parents[work]) & set(above_node.works)]
        # the key is the place in the level below, and then the place among
        # the partly performed works - a work the level below does not
        # name goes last
        order: Dict[WorkId, Tuple[int, int]] = {
            work: (position, 0)
            for position, work in enumerate(below)}
        if recording in order:
            partial_order = {
                work: position for position, work in enumerate(partial)}
            for work in leading:
                if work not in order and work in partial_order:
                    order[work] = (order[recording][0], partial_order[work])
        return sorted(
            leading,
            key=lambda work: (
                order.get(work, (len(below), 0)),
                work.mbid))
