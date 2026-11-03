# -*- coding: utf-8 -*-
"""Projection of a :class:`~hierarchy.graph.WorkGraph` into a hierarchy.

The legacy implementation keyed its hierarchy nodes by a *tuple* of work
ids. That made the identity of a node depend on the order the ids happened
to be discovered in, which is the root of a long series of defects:

* Two keys that meant the same node spelled differently, so the node was
  created twice and one copy was left empty.
* Renaming a key in place destroyed the key that other nodes depended on.
* Copying an entry aliased it, so writing to one key rewrote the other.

Here a node is identified by the *set* of works it stands for, so its
identity does not depend on discovery order.

What a node is
--------------
A node stands for a *set of works*: the works that sit at one place in the
hierarchy. A single work forms a node of its own. A work that MusicBrainz
lists under two parents is contained by a node holding both of them, which
is how a movement reached by two different routes stays a single place in
the tree.

So each work has its own node, and a node's parent is the node holding the
works that contain it.

That mirrors the legacy tuple keys -- the legacy ``partof`` mapping went
from a work's own id tuple to the id tuples of the works containing it --
except that the tuple order no longer matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Sequence, Set, TYPE_CHECKING

from .graph import NodeId, WorkGraph, WorkId

if TYPE_CHECKING:  # pragma: no cover - import cycle only needed for typing
    from .album import Performance


@dataclass
class Node:
    """One node of the projected hierarchy."""

    id: NodeId
    #: The works this node stands for.
    works: FrozenSet[WorkId]
    #: The nodes containing this one.
    parents: FrozenSet[NodeId] = frozenset()
    #: The nodes contained by this one.
    children: FrozenSet[NodeId] = frozenset()

    @property
    def is_fused(self) -> bool:
        """True when this node stands for more than one work."""
        return len(self.works) > 1

    @property
    def single(self) -> WorkId | None:
        """The only work of this node, or None if it fuses several works."""
        if len(self.works) == 1:
            return next(iter(self.works))
        return None


@dataclass
class Hierarchy:
    """A projected work hierarchy for one album."""

    nodes: Dict[NodeId, Node] = field(default_factory=dict)
    #: The node each work belongs to.
    node_of: Dict[WorkId, NodeId] = field(default_factory=dict)
    #: The node each recording's performance belongs to.
    node_of_performance: Dict[WorkId, NodeId] = field(default_factory=dict)
    #: The top level nodes, in node-id order.
    tops: List[NodeId] = field(default_factory=list)

    def node(self, node_id: NodeId) -> Node:
        return self.nodes[node_id]

    def ancestors(self, node_id: NodeId) -> FrozenSet[NodeId]:
        """Every node containing ``node_id``."""
        found: Set[NodeId] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            for parent in self.nodes[current].parents:
                if parent not in found:
                    found.add(parent)
                    stack.append(parent)
        return frozenset(found)

    def descendants(self, node_id: NodeId) -> FrozenSet[NodeId]:
        """Every node contained by ``node_id``."""
        found: Set[NodeId] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            for child in self.nodes[current].children:
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return frozenset(found)

    def path_to_top(self, node_id: NodeId) -> List[NodeId]:
        """The nodes from ``node_id`` up to a top level node.

        The shortest such path is returned, so the result does not depend on
        the order the hierarchy was discovered in. When several paths of the
        same length exist the one with the smallest node id is chosen, which
        keeps the result deterministic.
        """
        best: List[NodeId] = []
        queue: List[List[NodeId]] = [[node_id]]
        while queue:
            path = queue.pop(0)
            current = path[-1]
            parents = sorted(self.nodes[current].parents)
            if not parents:
                if not best or len(path) < len(best) or (
                        len(path) == len(best) and path < best):
                    best = path
                continue
            for parent in parents:
                if parent in path:
                    continue
                queue.append(path + [parent])
        return best


def reduce_parents(
        parents: FrozenSet[WorkId],
        accepted: Dict[WorkId, FrozenSet[WorkId]],
        ancestors: Dict[WorkId, FrozenSet[WorkId]],
        embedded_works: FrozenSet[WorkId] | None = None) -> FrozenSet[WorkId]:
    """Drop the parents that are redundant given the others.

    A parent ``x`` is dropped when another parent ``y`` is a better placement
    for the work:

    * ``x`` is an *ancestor* of ``y``. The work is linked both to a broad
      grouping work and to a specific sub-work that is itself part of that
      grouping, so the direct link to the grouping is redundant: it would
      otherwise fuse two levels of the hierarchy into one.
    * ``x`` is a *top level* work while ``y`` is *embedded* in a larger work.
      The work is linked both to a standalone work and to a movement of a
      bigger work that contains it; the embedded parent is the one that fits
      the hierarchy.
    * ``x`` reaches ``y``'s ancestors in a single step while ``y`` reaches
      them through a longer chain, so ``x`` is the flatter of the two
      placements. Every one of ``x``'s parents must be reachable that way:
      a parent that also leads somewhere ``y`` cannot reach carries a
      branch of its own, and dropping it would lose that branch.

    The reduction never returns an empty set, so a work always keeps at
    least one parent even if the data is cyclic.

    ``embedded_works`` limits the reduction to the works of one album. The
    legacy implementation needed that scope because a parent edge cached
    for a different album would otherwise make a standalone work look
    embedded. When it is not given, any work with a parent counts as
    embedded.
    """
    if len(parents) <= 1:
        return parents

    embedded = {
        candidate: bool(accepted.get(candidate))
        and (embedded_works is None or candidate in embedded_works)
        for candidate in parents}

    redundant: Set[WorkId] = set()
    for candidate in parents:
        for other in parents:
            if candidate == other:
                continue
            if candidate in ancestors.get(other, frozenset()):
                redundant.add(candidate)
                continue
            if not embedded[candidate] and embedded[other]:
                redundant.add(candidate)
                continue
            # The candidate reaches the other one's ancestors in a single step
            # while the other one reaches them through a longer chain, so the
            # candidate is the flatter of the two placements.
            #
            # Every one of the candidate's parents must be reachable that way.
            # A candidate that also leads somewhere the other one cannot reach
            # carries a branch of its own, and dropping it would lose that
            # branch from the tree.
            candidate_parents = accepted.get(candidate, frozenset())
            other_ancestors = ancestors.get(other, frozenset())
            if candidate_parents and other_ancestors:
                if all(parent in other_ancestors
                       for parent in candidate_parents):
                    if not any(parent in ancestors.get(candidate, frozenset())
                               for parent in accepted.get(other, frozenset())):
                        redundant.add(candidate)

    keep = frozenset(
        candidate for candidate in parents if candidate not in redundant)
    return keep if keep else parents


def reduce_redundant_parents(
        graph: WorkGraph,
        work: WorkId,
        parents: FrozenSet[WorkId],
        embedded_works: FrozenSet[WorkId] | None = None,
        use_arrangements: bool = True,
        use_medleys: bool = True,
        use_collections: bool = True,
        accepted: Dict[WorkId, FrozenSet[WorkId]] | None = None) -> FrozenSet[WorkId]:
    """Drop the parents of ``work`` that are redundant given the others.

    See :func:`reduce_parents` for the rules.
    """
    if len(parents) <= 1:
        return parents

    if accepted is None:
        accepted = graph.acyclic_parent_edges(
            use_arrangements=use_arrangements,
            use_medleys=use_medleys,
            use_collections=use_collections)

    return reduce_parents(
        parents,
        accepted,
        _ancestors_for(accepted, parents),
        embedded_works=embedded_works)


def _ancestors_for(
        accepted: Dict[WorkId, FrozenSet[WorkId]],
        works: FrozenSet[WorkId]) -> Dict[WorkId, FrozenSet[WorkId]]:
    """The ancestors of each of ``works`` within the given parent edges."""
    return {work: _ancestors_in(accepted, work) for work in works}


def _ancestors_in(
        accepted: Dict[WorkId, FrozenSet[WorkId]],
        work: WorkId) -> FrozenSet[WorkId]:
    """The ancestors of ``work`` within the given parent edges."""
    found: Set[WorkId] = set()
    stack: List[WorkId] = [work]
    while stack:
        node = stack.pop()
        for parent in accepted.get(node, frozenset()):
            if parent not in found:
                found.add(parent)
                stack.append(parent)
    return frozenset(found)


def _performance_node(
        performance: "Performance",
        accepted: Dict[WorkId, FrozenSet[WorkId]],
        hierarchy: Hierarchy) -> NodeId:
    """The node of a recording's performance, and its place in the tree.

    The node holds the ids standing for the works the recording performs: the
    work itself, or the recording id when the work is only partially
    performed, because such a recording is not the work.

    Its parent holds the union of what those works contribute:

    * a work performed in full contributes its own parents;
    * a work performed both in full and in part contributes itself, because
      the recording id stands in for the work one level below it;
    * a work performed only in part contributes itself too. The recording
      id stands in for the work one level below it, so the work is the
      parent of the recording id - and its own parents are one level
      above that.

    The parent's members are reduced like any other node's parent set, and
    that is what makes the rule work. A work performed only in part sits
    between the recording id and the work's own parents, so it is the
    *parent* of them rather than their ancestor, and the reduction keeps
    it. But when the recording also performs a work in full, that work's
    parents are contributed as well, and the partly performed work is
    then an ancestor of them - so the reduction drops it in their favour,
    which is what the legacy does.

    The legacy was run on both cases: a recording that performs only a
    partly performed work gets the work one level above the recording
    id, and one that also performs a work in full gets that work's
    parents instead.
    """
    members = performance.members
    node_id = NodeId(members)
    if node_id not in hierarchy.nodes:
        hierarchy.nodes[node_id] = Node(id=node_id, works=members)

    # A work performed only in part is the parent of the recording id:
    # the recording id stands in for the work one level below it, so the
    # work sits one level above. The legacy does the same - it looks the
    # partly performed work up as a node of its own and the recording's
    # node points at it.
    partial_works = [
        work for work in performance.works
        if performance.contribution(work) == 'none']

    contributions: Set[WorkId] = set()
    for work in performance.works:
        kind = performance.contribution(work)
        if kind == 'self':
            contributions.add(work)
        elif kind == 'parents':
            contributions |= accepted.get(work, frozenset())
    if contributions:
        # The works performed in full decide the parent. A work performed
        # only in part is the parent of the recording id only when there
        # is no such work - and the legacy works it that way round: it
        # takes the parents of the works performed in full, and looks a
        # partly performed work up as a node of its own.
        reduced = reduce_parents(
            frozenset(contributions),
            accepted,
            _ancestors_for(accepted, frozenset(contributions)))
        container = NodeId(reduced)
        if container == node_id:
            return node_id
        if container not in hierarchy.nodes:
            hierarchy.nodes[container] = Node(
                id=container, works=container.works)
        node = hierarchy.nodes[node_id]
        node.parents = frozenset({container})
        hierarchy.nodes[container].children = (
            hierarchy.nodes[container].children | {node_id})
        return node_id

    if not partial_works:
        return node_id

    # Only partly performed works: the recording id stands in for each of
    # them, so they are the parent of the recording id. They are reduced
    # like any other node's parent set, so that a chain does not visit a
    # work twice - a partly performed work that is the parent of another
    # one would otherwise put that other one in the chain twice.
    reduced = reduce_parents(
        frozenset(partial_works),
        accepted,
        _ancestors_for(accepted, frozenset(partial_works)))
    container = NodeId(reduced)
    if container == node_id:
        return node_id
    if container not in hierarchy.nodes:
        hierarchy.nodes[container] = Node(id=container, works=container.works)
    node = hierarchy.nodes[node_id]
    node.parents = frozenset({container})
    hierarchy.nodes[container].children = (
        hierarchy.nodes[container].children | {node_id})
    return node_id


def project(
        graph: WorkGraph,
        use_arrangements: bool = True,
        use_medleys: bool = True,
        use_collections: bool = True,
        embedded_works: FrozenSet[WorkId] | None = None,
        reduce_redundant: bool = True,
        performances: Sequence["Performance"] | None = None) -> Hierarchy:
    """Project a work graph into a hierarchy.

    Each work forms a node of its own. The node containing a work is the
    node holding the works that contain it, so a movement is contained by
    its act, which is contained by the opera. A work with two parents is
    contained by a node holding both of them, which keeps a movement that
    was reached by two routes in one place in the tree.

    The parent edges used are the cycle-free ones from
    :meth:`~hierarchy.graph.WorkGraph.acyclic_parent_edges`, so the
    projection cannot contain a cycle.

    When ``reduce_redundant`` is set, a work's parents are reduced first:
    a parent that is an ancestor of another parent is dropped, as is a top
    level parent when another parent is embedded in a larger work, as is the
    flatter of two parents that reach the same work by chains of different
    length. The parents of a container are reduced the same way. Without
    that reduction such a work would sit one level flatter than its
    siblings, and a chain could visit the same work twice.

    ``embedded_works`` limits the reduction to the works of one album. The
    legacy implementation needed that scope because a parent edge cached
    for a different album would otherwise make a standalone work look
    embedded.

    ``performances`` are the recordings of the album. A recording that
    performs several works has one node holding all of them, so it has one
    chain rather than one per work - see :class:`hierarchy.album.Performance`
    for what a performance's node holds and what its parent holds. Without
    them every work forms a node of its own, which is the plain work level
    view of the hierarchy.
    """
    accepted = graph.acyclic_parent_edges(
        use_arrangements=use_arrangements,
        use_medleys=use_medleys,
        use_collections=use_collections)

    if reduce_redundant:
        reduced: Dict[WorkId, FrozenSet[WorkId]] = {}
        for work, parents in accepted.items():
            if len(parents) <= 1:
                reduced[work] = parents
                continue
            reduced[work] = reduce_redundant_parents(
                graph,
                work,
                parents,
                embedded_works=embedded_works,
                accepted=accepted)
        accepted = reduced

    hierarchy = Hierarchy()

    # Each work is a node of its own.
    node_of: Dict[WorkId, NodeId] = {
        work: NodeId(frozenset({work})) for work in graph.works()}

    for node_id in node_of.values():
        hierarchy.nodes[node_id] = Node(id=node_id, works=node_id.works)

    # The node containing a work is the node holding the works that contain
    # it. When a work has two parents that is a node holding both, so the
    # work has a single place in the tree.
    for work in graph.works():
        parents = accepted.get(work, frozenset())
        if not parents:
            continue
        container = NodeId(frozenset(parents))
        if container not in hierarchy.nodes:
            hierarchy.nodes[container] = Node(id=container, works=container.works)
        node = hierarchy.nodes[node_of[work]]
        node.parents = frozenset({container})
        hierarchy.nodes[container].children = (
            hierarchy.nodes[container].children | {node_of[work]})

    # A recording's performance forms a node of its own, holding the works
    # it performs. A recording that performs several works therefore has one
    # node and one chain, instead of one chain per work.
    node_of_performance: Dict[WorkId, NodeId] = {}
    for performance in performances or ():
        node = _performance_node(performance, accepted, hierarchy)
        node_of_performance[performance.key] = node

    # A node holding several works -- the container of a work with two
    # parents -- is itself contained by the node holding the parents of
    # those works. Without this the container would be an orphan root and
    # everything below it would be invisible from the top of the tree.
    #
    # The parents of that container are reduced as well. A work reached by
    # two routes can have two parents that lead to the same work by
    # different chains, and keeping both would put the same work twice in
    # one chain.
    pending = list(hierarchy.nodes)
    performance_nodes = frozenset(node_of_performance.values())
    while pending:
        node_id = pending.pop()
        node = hierarchy.nodes[node_id]
        if node_id in performance_nodes:
            # A performance node's parent was worked out from what its works
            # contribute, which is not the same as the parents of the works
            # it holds.
            continue
        container_works: Set[WorkId] = set()
        for work in node.works:
            container_works |= accepted.get(work, frozenset())
        if not container_works:
            continue
        container_works = reduce_parents(
            frozenset(container_works),
            accepted,
            _ancestors_for(accepted, frozenset(container_works)),
            embedded_works=embedded_works) if reduce_redundant else frozenset(
                container_works)
        container = NodeId(frozenset(container_works))
        if container == node_id:
            continue
        if container not in hierarchy.nodes:
            hierarchy.nodes[container] = Node(id=container, works=container.works)
            pending.append(container)
        node.parents = frozenset({container})
        hierarchy.nodes[container].children = (
            hierarchy.nodes[container].children | {node_id})

    hierarchy.node_of = node_of
    hierarchy.node_of_performance = node_of_performance

    # The top level nodes are the ones nothing contains.
    hierarchy.tops = sorted(
        node_id for node_id, node in hierarchy.nodes.items()
        if not node.parents)

    return hierarchy
