# -*- coding: utf-8 -*-
"""Typed multigraph of MusicBrainz works.

This module is deliberately free of any Picard dependency: it operates purely
on work identifiers and relation edges, so it can be unit tested without a
running Picard and shared by the v2 and v3 plugins alike.

Why a graph and not a tree
--------------------------
MusicBrainz models works as a *graph* with several relation types, of which
only one is a true parent edge. The legacy implementation folded every
relation into a single ``parent`` list and then had to repair the resulting
corruption with ad-hoc guards, which is where its order-dependence and
silent tag loss came from. Here the edge types are kept apart, and the
hierarchy is *projected* from the parent edges only.

Edge conventions
----------------
A relation read from MusicBrainz links a source work to a target work. For
the ``parts`` relation type the two directions mean:

``direction == 'backward'``
    The source work is a *part of* the target work, so the target is the
    source's parent.
``direction == 'forward'``
    The source work has the target as one of its parts, so the target is the
    source's child.

Other relation types (``arrangement``, ``based on``, ``revision of``, ...)
are retained verbatim under :attr:`WorkGraph.other_edges` but take no part in
the hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, Iterator, List, Set, Tuple

#: Relation type that carries hierarchical parenthood.
PARTS_RELATION = 'parts'

#: Relation type for a work that is an arrangement of another work.
ARRANGEMENT_RELATION = 'arrangement'

#: Relation type for a work that is a medley of other works.
MEDLEY_RELATION = 'medley'

#: Attribute marking a "part of collection" relation.
COLLECTION_ATTRIBUTE = 'part of collection'

#: Direction of a ``parts`` relation that points from a work to its parent.
BACKWARD = 'backward'

#: Direction of a ``parts`` relation that points from a work to its child.
FORWARD = 'forward'

# Colours used by the depth-first search that breaks cycles.
_WHITE = 0
_GREY = 1
_BLACK = 2


@dataclass(frozen=True, order=True)
class WorkId:
    """Identity of a single MusicBrainz work."""

    mbid: str

    def __str__(self) -> str:
        return self.mbid


@dataclass(frozen=True)
class NodeId:
    """Identity of a hierarchy node.

    A node is identified by the *set* of works it stands for, so its identity
    is that set. Using a frozenset makes the identity independent of the
    order the works were discovered in: ``('a', 'b')`` and ``('b', 'a')`` are
    the same node. That is what makes "which top work won" a well defined
    question instead of a race.
    """

    works: FrozenSet[WorkId]

    def __str__(self) -> str:
        return '{%s}' % ', '.join(sorted(w.mbid for w in self.works))

    def _sort_key(self) -> Tuple[str, ...]:
        return tuple(sorted(w.mbid for w in self.works))

    def __lt__(self, other: "NodeId") -> bool:
        if not isinstance(other, NodeId):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __le__(self, other: "NodeId") -> bool:
        if not isinstance(other, NodeId):
            return NotImplemented
        return self._sort_key() <= other._sort_key()

    def __gt__(self, other: "NodeId") -> bool:
        if not isinstance(other, NodeId):
            return NotImplemented
        return self._sort_key() > other._sort_key()

    def __ge__(self, other: "NodeId") -> bool:
        if not isinstance(other, NodeId):
            return NotImplemented
        return self._sort_key() >= other._sort_key()

    @property
    def single(self) -> WorkId | None:
        """The only work of this node, or None if it fuses several works."""
        if len(self.works) == 1:
            return next(iter(self.works))
        return None

    @property
    def is_fused(self) -> bool:
        """True when this node stands for more than one work."""
        return len(self.works) > 1


@dataclass(frozen=True)
class Relation:
    """A single relation read from MusicBrainz."""

    source: WorkId
    relation_type: str
    direction: str
    target: WorkId
    attributes: FrozenSet[str] = frozenset()

    @property
    def is_collection(self) -> bool:
        """True for a "part of collection" relation."""
        return COLLECTION_ATTRIBUTE in self.attributes


@dataclass(frozen=True)
class DroppedEdge:
    """A parent edge excluded from the hierarchy projection.

    Recorded rather than silently discarded so the condition can be reported
    to the user instead of surfacing as missing tags.
    """

    child: WorkId
    parent: WorkId
    reason: str


@dataclass
class WorkGraph:
    """A typed multigraph of works and their relations.

    ``parents`` and ``children`` hold only genuine hierarchical edges.
    ``other_edges`` holds every remaining relation type, keyed by relation
    type, and never influences the hierarchy.
    """

    parents: Dict[WorkId, Set[WorkId]] = field(default_factory=dict)
    children: Dict[WorkId, Set[WorkId]] = field(default_factory=dict)
    other_edges: Dict[WorkId, Dict[str, Set[WorkId]]] = field(default_factory=dict)
    relations: List[Relation] = field(default_factory=list)
    dropped_edges: List[DroppedEdge] = field(default_factory=list)

    # -- construction ----------------------------------------------------

    def add_work(self, work: WorkId) -> None:
        """Register a work so it is known even without any relation."""
        self.parents.setdefault(work, set())
        self.children.setdefault(work, set())

    def add_parent_edge(self, child: WorkId, parent: WorkId) -> None:
        """Record that ``child`` is a part of ``parent``."""
        self.add_relation(child, PARTS_RELATION, BACKWARD, parent)

    def add_other_edge(self, source: WorkId, relation_type: str, target: WorkId) -> None:
        """Record a non-hierarchical relation (arrangement, based on, ...)."""
        self.add_work(source)
        self.add_work(target)
        self.other_edges.setdefault(source, {}).setdefault(relation_type, set()).add(target)

    def add_relation(
            self,
            source: WorkId,
            relation_type: str,
            direction: str,
            target: WorkId,
            attributes: Iterable[str] = ()) -> None:
        """Record a relation as read from MusicBrainz.

        Only ``parts`` relations contribute to the hierarchy directly.
        Everything else is retained so that the parent resolution can fall
        back to it, and so that the relations remain available for the
        artist and genre handling.
        """
        relation = Relation(
            source=source,
            relation_type=relation_type,
            direction=direction,
            target=target,
            attributes=frozenset(attributes))
        self.relations.append(relation)
        self.add_work(source)
        self.add_work(target)
        if relation_type == PARTS_RELATION and direction == BACKWARD:
            self.parents[source].add(target)
            self.children[target].add(source)
        elif relation_type == PARTS_RELATION and direction == FORWARD:
            self.parents[target].add(source)
            self.children[source].add(target)
        else:
            self.other_edges.setdefault(source, {}).setdefault(
                relation_type, set()).add(target)

    def relations_of(
            self,
            work: WorkId,
            relation_type: str,
            direction: str | None = None) -> List[Relation]:
        """The relations of the given type leaving ``work``."""
        return [
            relation for relation in self.relations
            if relation.source == work
            and relation.relation_type == relation_type
            and (direction is None or relation.direction == direction)
        ]

    def relation_targets(
            self,
            work: WorkId,
            relation_type: str,
            direction: str | None = None) -> Set[WorkId]:
        """The targets of the given relation type leaving ``work``."""
        return {
            relation.target
            for relation in self.relations_of(work, relation_type, direction)
        }

    def resolve_parents(
            self,
            use_arrangements: bool = True,
            use_medleys: bool = True,
            use_collections: bool = True) -> Dict[WorkId, FrozenSet[WorkId]]:
        """The parent of every work, following MusicBrainz's conventions.

        A work's parent is found by trying the relation types in order:

        1. ``parts`` relations, the ordinary case.
        2. ``arrangement`` relations, for a work that is an arrangement of
           another. Only used when arrangements are enabled.
        3. ``medley`` relations, for a work that is a medley of others. Only
           used when medleys are enabled.

        The first type that yields a parent wins, so a work that has both a
        ``parts`` and an ``arrangement`` relation is treated as a part, which
        is what MusicBrainz intends.

        A ``part of collection`` relation names a grouping work rather than a
        genuine parent, so it is ignored when collections are disabled.
        """
        resolved: Dict[WorkId, FrozenSet[WorkId]] = {}
        for work in self.works():
            parents: Set[WorkId] = set()

            for relation in self.relations_of(work, PARTS_RELATION, BACKWARD):
                if relation.is_collection and not use_collections:
                    continue
                parents.add(relation.target)

            if not parents and use_arrangements:
                parents = self.relation_targets(
                    work, ARRANGEMENT_RELATION, BACKWARD)

            if not parents and use_medleys:
                for relation in self.relations_of(work, MEDLEY_RELATION):
                    if relation.direction == BACKWARD:
                        continue
                    parents.add(relation.target)

            resolved[work] = frozenset(parents)
        return resolved

    # -- queries ---------------------------------------------------------

    def works(self) -> Set[WorkId]:
        """Every work known to the graph."""
        return set(self.parents) | set(self.children)

    def ancestors(self, work: WorkId) -> FrozenSet[WorkId]:
        """Works reachable from ``work`` by following parent edges upwards.

        These are the works that contain ``work``: for a movement, its part,
        its act and its opera.

        The start work is included in the result when it is reachable from
        itself, which happens exactly when the graph contains a cycle through
        it.

        Reachability is computed iteratively, so a deep or cyclic graph cannot
        exhaust the interpreter stack, and a cycle terminates rather than
        looping forever.

        This walks the raw ``parts`` relations, so a cycle-closing edge is
        included and an arrangement is not followed. Code that walks the
        *hierarchy* should use :meth:`hierarchy_ancestors` instead.
        """
        found: Set[WorkId] = set()
        stack: List[WorkId] = [work]
        while stack:
            node = stack.pop()
            for parent in self.parents.get(node, ()):
                if parent not in found:
                    found.add(parent)
                    stack.append(parent)
        return frozenset(found)

    def descendants(self, work: WorkId) -> FrozenSet[WorkId]:
        """Works contained by ``work``, found by following child edges down.

        This is the counterpart of :meth:`ancestors`: for an opera, its acts,
        its scenes and its movements.

        As with :meth:`ancestors`, this walks the raw graph. Use
        :meth:`hierarchy_descendants` to walk the projected hierarchy.
        """
        found: Set[WorkId] = set()
        stack: List[WorkId] = [work]
        while stack:
            node = stack.pop()
            for child in self.children.get(node, ()):
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return frozenset(found)

    def hierarchy_ancestors(self, work: WorkId) -> FrozenSet[WorkId]:
        """Ancestors of ``work`` within the projected hierarchy.

        Unlike :meth:`ancestors` this follows the resolved parents (so an
        arrangement is followed) and ignores the parent edges that were
        dropped to break a cycle. The result is therefore always acyclic and
        code that walks it cannot become cycle-dependent.
        """
        accepted = self.acyclic_parent_edges()
        found: Set[WorkId] = set()
        stack: List[WorkId] = [work]
        while stack:
            node = stack.pop()
            for parent in accepted.get(node, ()):
                if parent not in found:
                    found.add(parent)
                    stack.append(parent)
        return frozenset(found)

    def hierarchy_descendants(self, work: WorkId) -> FrozenSet[WorkId]:
        """Descendants of ``work`` within the projected hierarchy.

        The counterpart of :meth:`hierarchy_ancestors`, and free of the
        cycle-dependence that :meth:`descendants` can carry.
        """
        accepted = self.acyclic_parent_edges()
        children: Dict[WorkId, Set[WorkId]] = {}
        for child, parents in accepted.items():
            for parent in parents:
                children.setdefault(parent, set()).add(child)
        found: Set[WorkId] = set()
        stack: List[WorkId] = [work]
        while stack:
            node = stack.pop()
            for child in children.get(node, ()):
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return frozenset(found)

    def acyclic_parent_edges(
            self,
            use_arrangements: bool = True,
            use_medleys: bool = True,
            use_collections: bool = True) -> Dict[WorkId, FrozenSet[WorkId]]:
        """Parent edges that do not close a cycle.

        The parents are resolved first (see :meth:`resolve_parents`), so an
        arrangement or a medley counts as a parent when those are enabled.

        A depth-first search over the parent edges visits works in a
        deterministic order (sorted by id) and drops exactly those edges that
        point back to a work already on the current search path -- the
        classical *back edges*. Every other edge is kept, including the extra
        parents of a work that legitimately has several.

        This matters because the obvious per-edge test ("drop this parent if
        the child is an ancestor of the parent") is wrong: inside a cycle every
        edge satisfies that test, so it would remove the whole cycle and
        leave the work disconnected from its album. Breaking one back edge
        per cycle keeps the hierarchy connected.

        Because the traversal order is fixed by the work ids rather than by
        the order the edges were discovered, the result does not depend on
        the order the MusicBrainz lookups happen to complete in.

        The search is iterative, so a deeply nested work hierarchy cannot
        exhaust the interpreter stack.
        """
        resolved = self.resolve_parents(
            use_arrangements=use_arrangements,
            use_medleys=use_medleys,
            use_collections=use_collections)

        accepted: Dict[WorkId, Set[WorkId]] = {
            work: set() for work in self.works()}
        colour: Dict[WorkId, int] = {work: _WHITE for work in self.works()}
        dropped: Set[Tuple[WorkId, WorkId]] = set()

        for root in sorted(self.works()):
            if colour[root] != _WHITE:
                continue
            # Explicit stack of (work, iterator over its parents). Each frame
            # resumes where the previous one stopped, which is what gives the
            # traversal its depth-first character.
            stack: List[Tuple[WorkId, Iterator[WorkId]]] = [
                (root, iter(sorted(resolved.get(root, frozenset()))))
            ]
            colour[root] = _GREY
            while stack:
                work, parents = stack[-1]
                parent = next(parents, None)
                if parent is None:
                    colour[work] = _BLACK
                    stack.pop()
                    continue
                if colour[parent] == _GREY:
                    # Back edge: following it would let `work` reach itself.
                    dropped.add((work, parent))
                    continue
                accepted[work].add(parent)
                if colour[parent] == _WHITE:
                    colour[parent] = _GREY
                    stack.append(
                        (parent, iter(sorted(resolved.get(parent, frozenset())))))

        self.dropped_edges = [
            DroppedEdge(
                child=child,
                parent=parent,
                reason='parent is a descendant of the child')
            for child, parent in sorted(dropped)
        ]
        return {work: frozenset(parents) for work, parents in accepted.items()}
