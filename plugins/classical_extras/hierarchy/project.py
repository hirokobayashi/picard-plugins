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
from typing import Dict, FrozenSet, List, Set

from .graph import NodeId, WorkGraph, WorkId


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
    #: The top level nodes, in discovery order.
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

    A parent ``x`` is dropped when another parent ``y`` is a better placement
    for the work, in either of two ways:

    * ``x`` is an *ancestor* of ``y*. The work is linked both to a broad
      grouping work and to a specific sub-work that is itself part of that
      grouping, so the direct link to the grouping is redundant: it would
      otherwise fuse two levels of the hierarchy into one.
    * ``x`` is a *top level* work while ``y`` is *embedded* in a larger work.
      The work is linked both to a standalone work and to a movement of a
      bigger work that contains it; the embedded parent is the one that fits
      the hierarchy.

    The reduction never returns an empty set, so a work always keeps at
    least one parent even if the data is cyclic.

    ``embedded_works`` limits the reduction to the works of one album. The
    legacy implementation needed that scope because a parent edge cached
    for a different album would otherwise make a standalone work look
    embedded. When it is not given, any work with a parent counts as
    embedded.

    ``accepted`` may be supplied to avoid recomputing the parent edges for
    every call.
    """
    if len(parents) <= 1:
        return parents

    if accepted is None:
        accepted = graph.acyclic_parent_edges(
            use_arrangements=use_arrangements,
            use_medleys=use_medleys,
            use_collections=use_collections)

    ancestors = {
        candidate: _ancestors_in(accepted, candidate)
        for candidate in parents}

    embedded = {
        candidate: bool(accepted.get(candidate))
        and (embedded_works is None or candidate in embedded_works)
        for candidate in parents}

    def redundant(candidate: WorkId) -> bool:
        for other in parents:
            if other == candidate:
                continue
            if candidate in ancestors[other]:
                return True
            if not embedded[candidate] and embedded[other]:
                return True
        return False

    keep = frozenset(
        candidate for candidate in parents if not redundant(candidate))
    return keep if keep else parents


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


def project(
        graph: WorkGraph,
        use_arrangements: bool = True,
        use_medleys: bool = True,
        use_collections: bool = True,
        embedded_works: FrozenSet[WorkId] | None = None,
        reduce_redundant: bool = True) -> Hierarchy:
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
    level parent when another parent is embedded in a larger work. Without
    that reduction such a work would sit one level flatter than its
    siblings.

    ``embedded_works`` limits the reduction to the works of one album. The
    legacy implementation needed that scope because a parent edge cached
    for a different album would otherwise make a standalone work look
    embedded.
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

    # A node holding several works -- the container of a work with two
    # parents -- is itself contained by the node holding the parents of
    # those works. Without this the container would be an orphan root and
    # everything below it would be invisible from the top of the tree.
    pending = list(hierarchy.nodes)
    while pending:
        node_id = pending.pop()
        node = hierarchy.nodes[node_id]
        container_works: Set[WorkId] = set()
        for work in node.works:
            container_works |= accepted.get(work, frozenset())
        if not container_works:
            continue
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

    # The top level nodes are the ones nothing contains.
    hierarchy.tops = sorted(
        node_id for node_id, node in hierarchy.nodes.items()
        if not node.parents)

    return hierarchy
