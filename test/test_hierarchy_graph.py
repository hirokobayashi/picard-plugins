#!/usr/bin/env python
# coding: utf-8
"""Tests for the Picard-independent work-hierarchy graph.

These cover the three situations the legacy implementation handled with
ad-hoc guards, plus the property that matters most for the Picard 3 port:
the hierarchy must not depend on the order in which the MusicBrainz lookups
happen to return.
"""

import glob
import json
import os
import random
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "plugins", "classical_extras"))

from hierarchy.graph import (  # noqa: E402
    NodeId,
    WorkGraph,
    WorkId,
)
from hierarchy.project import (  # noqa: E402
    project,
    reduce_redundant_parents,
)


def w(mbid):
    return WorkId(mbid)


class WorkGraphTestCase(unittest.TestCase):
    """Construction and relation typing."""

    def test_add_relation_backward_parts_makes_parent(self):
        graph = WorkGraph()
        graph.add_relation(w("child"), "parts", "backward", w("parent"))
        self.assertEqual(graph.parents[w("child")], {w("parent")})
        self.assertEqual(graph.children[w("parent")], {w("child")})

    def test_add_relation_forward_parts_makes_child(self):
        graph = WorkGraph()
        graph.add_relation(w("parent"), "parts", "forward", w("child"))
        self.assertEqual(graph.parents[w("child")], {w("parent")})
        self.assertEqual(graph.children[w("parent")], {w("child")})

    def test_non_hierarchical_relation_is_not_a_parent(self):
        graph = WorkGraph()
        graph.add_relation(w("arr"), "arrangement", "forward", w("orig"))
        self.assertEqual(graph.parents[w("arr")], set())
        self.assertEqual(graph.parents[w("orig")], set())
        self.assertEqual(
            graph.other_edges[w("arr")]["arrangement"], {w("orig")})

    def test_works_includes_both_endpoints(self):
        graph = WorkGraph()
        graph.add_relation(w("a"), "parts", "backward", w("b"))
        self.assertEqual(graph.works(), {w("a"), w("b")})

    def test_duplicate_edge_is_idempotent(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("a"), w("b"))
        self.assertEqual(graph.parents[w("a")], {w("b")})


class CycleBreakingTestCase(unittest.TestCase):
    """Cycles must be broken without disconnecting the hierarchy."""

    def _edges(self, graph):
        """Return the accepted parent edges as a set of (child, parent)."""
        return {
            (child, parent)
            for child, parents in graph.acyclic_parent_edges().items()
            for parent in parents
        }

    def test_self_loop_is_dropped(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("a"))
        edges = self._edges(graph)
        self.assertNotIn((w("a"), w("a")), edges)
        self.assertEqual(len(graph.dropped_edges), 1)

    def test_two_node_cycle_keeps_one_edge(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("a"))
        edges = self._edges(graph)
        # Exactly one edge survives: the cycle is broken, not annihilated.
        self.assertEqual(len(edges), 1)
        self.assertEqual(len(graph.dropped_edges), 1)

    def test_three_node_cycle_keeps_two_edges(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("c"))
        graph.add_parent_edge(w("c"), w("a"))
        edges = self._edges(graph)
        self.assertEqual(len(edges), 2)
        self.assertEqual(len(graph.dropped_edges), 1)

    def test_cycle_leaves_works_connected(self):
        """The whole point: a cycle must not orphan the works below it.

        The legacy per-edge guard ("drop this parent if the child is an
        ancestor of the parent") removes *every* edge of a cycle, because
        inside a cycle each edge satisfies that test. The result is a work
        with no parent at all, disconnected from the rest of the album.
        """
        graph = WorkGraph()
        graph.add_parent_edge(w("opera"), w("act"))
        graph.add_parent_edge(w("act"), w("scene"))
        graph.add_parent_edge(w("scene"), w("opera"))
        graph.add_parent_edge(w("movement"), w("scene"))
        edges = self._edges(graph)
        # Exactly one edge of the cycle is dropped, and every other edge
        # survives, so the movement is still attached to the scene and the
        # scene is still attached to the cycle.
        self.assertEqual(
            edges,
            {(w("act"), w("scene")),
             (w("scene"), w("opera")),
             (w("movement"), w("scene"))})
        self.assertEqual(len(graph.dropped_edges), 1)
        # The dropped edge is chosen deterministically: the search starts at
        # the alphabetically first work, so "opera" is on the path when its
        # own parent is considered.
        self.assertEqual(
            (graph.dropped_edges[0].child, graph.dropped_edges[0].parent),
            (w("opera"), w("act")))

    def test_cycle_does_not_orphan_descendants(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("a"))
        graph.add_parent_edge(w("c"), w("b"))
        graph.add_parent_edge(w("d"), w("c"))
        edges = self._edges(graph)
        self.assertIn((w("c"), w("b")), edges)
        self.assertIn((w("d"), w("c")), edges)

    def test_dropped_edge_is_reported(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("a"))
        graph.acyclic_parent_edges()
        self.assertEqual(len(graph.dropped_edges), 1)
        dropped = graph.dropped_edges[0]
        self.assertEqual({dropped.child, dropped.parent}, {w("a"), w("b")})
        self.assertTrue(dropped.reason)


class DeepHierarchyTestCase(unittest.TestCase):
    """A deeply nested hierarchy must not exhaust the interpreter stack.

    The legacy implementation computed the depth of a work with a recursive
    walk that had no visited set, so it could neither survive a deep
    hierarchy nor a cycle.
    """

    def test_deep_chain_does_not_overflow(self):
        depth = 5000
        graph = WorkGraph()
        for index in range(depth):
            graph.add_parent_edge(
                w("w%06d" % index), w("w%06d" % (index + 1)))
        result = graph.acyclic_parent_edges()
        self.assertEqual(sum(len(v) for v in result.values()), depth)
        self.assertEqual(graph.dropped_edges, [])

    def test_deep_chain_ancestors_do_not_overflow(self):
        depth = 5000
        graph = WorkGraph()
        for index in range(depth):
            graph.add_parent_edge(
                w("w%06d" % index), w("w%06d" % (index + 1)))
        self.assertEqual(len(graph.ancestors(w("w000000"))), depth)


class ConvergentPathTestCase(unittest.TestCase):
    """Two branches reaching one ancestor is not a cycle.

    This is the case the legacy circular-reference guard got wrong: it
    stripped the shared parent and orphaned the whole subtree, so the track
    lost its top work and every tag.
    """

    def _edges(self, graph):
        return {
            (child, parent)
            for child, parents in graph.acyclic_parent_edges().items()
            for parent in parents
        }

    def test_convergent_paths_keep_the_shared_parent(self):
        #        opera
        #       /     \
        #   akt1      akt3
        #       \     /
        #        arrangement
        graph = WorkGraph()
        graph.add_parent_edge(w("opera"), w("akt1"))
        graph.add_parent_edge(w("opera"), w("akt3"))
        graph.add_parent_edge(w("arrangement"), w("akt1"))
        graph.add_parent_edge(w("arrangement"), w("akt3"))
        edges = self._edges(graph)
        self.assertIn((w("arrangement"), w("akt1")), edges)
        self.assertIn((w("arrangement"), w("akt3")), edges)
        self.assertEqual(graph.dropped_edges, [])

    def test_diamond_is_not_a_cycle(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("top"), w("left"))
        graph.add_parent_edge(w("top"), w("right"))
        graph.add_parent_edge(w("bottom"), w("left"))
        graph.add_parent_edge(w("bottom"), w("right"))
        self.assertEqual(len(self._edges(graph)), 4)
        self.assertEqual(graph.dropped_edges, [])

    def test_multiple_parents_are_all_kept(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("work"), w("p1"))
        graph.add_parent_edge(w("work"), w("p2"))
        graph.add_parent_edge(w("work"), w("p3"))
        self.assertEqual(len(self._edges(graph)), 3)
        self.assertEqual(graph.dropped_edges, [])


class OrderIndependenceTestCase(unittest.TestCase):
    """The hierarchy must not depend on the order edges are discovered.

    Picard resolves the work lookups asynchronously, so the same release can
    be processed in any order. The legacy implementation produced different
    tags depending on that order.
    """

    def _shuffled_graphs(self, edges, seed_count=12):
        """Yield graphs built from ``edges`` in many different orders."""
        for seed in range(seed_count):
            shuffled = list(edges)
            random.Random(seed).shuffle(shuffled)
            graph = WorkGraph()
            for child, parent in shuffled:
                graph.add_parent_edge(child, parent)
            yield graph

    def _edge_set(self, graph):
        return {
            (child, parent)
            for child, parents in graph.acyclic_parent_edges().items()
            for parent in parents
        }

    def test_cycle_result_is_independent_of_edge_order(self):
        edges = [
            (w("a"), w("b")),
            (w("b"), w("c")),
            (w("c"), w("a")),
            (w("d"), w("c")),
        ]
        results = set()
        for graph in self._shuffled_graphs(edges):
            results.add(frozenset(self._edge_set(graph)))
        self.assertEqual(len(results), 1, "cycle breaking depended on edge order")

    def test_convergent_result_is_independent_of_edge_order(self):
        edges = [
            (w("opera"), w("akt1")),
            (w("opera"), w("akt3")),
            (w("arr"), w("akt1")),
            (w("arr"), w("akt3")),
        ]
        results = set()
        for graph in self._shuffled_graphs(edges):
            results.add(frozenset(self._edge_set(graph)))
        self.assertEqual(len(results), 1, "hierarchy depended on edge order")

    def test_dropped_edge_set_is_independent_of_edge_order(self):
        edges = [
            (w("a"), w("b")),
            (w("b"), w("c")),
            (w("c"), w("a")),
        ]
        results = set()
        for graph in self._shuffled_graphs(edges):
            graph.acyclic_parent_edges()
            results.add(frozenset(
                (d.child, d.parent) for d in graph.dropped_edges))
        self.assertEqual(len(results), 1, "dropped edge depended on edge order")


class NodeIdTestCase(unittest.TestCase):

    def test_node_identity_is_order_independent(self):
        a = NodeId(frozenset({w("x"), w("y")}))
        b = NodeId(frozenset({w("y"), w("x")}))
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))

    def test_single_and_fused(self):
        self.assertEqual(NodeId(frozenset({w("x")})).single, w("x"))
        self.assertFalse(NodeId(frozenset({w("x")})).is_fused)
        fused = NodeId(frozenset({w("x"), w("y")}))
        self.assertIsNone(fused.single)
        self.assertTrue(fused.is_fused)


class DescendantsTestCase(unittest.TestCase):

    def test_ancestors_terminates_on_cycle(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("c"))
        graph.add_parent_edge(w("c"), w("a"))
        result = graph.ancestors(w("a"))
        # The cycle is closed, so "a" is genuinely reachable from "a"; the
        # traversal must terminate and report every member of the cycle.
        self.assertEqual(result, frozenset({w("a"), w("b"), w("c")}))

    def test_ancestors_terminates_on_self_loop(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("a"))
        self.assertEqual(graph.ancestors(w("a")), frozenset({w("a")}))

    def test_ancestors_follows_parents_upwards(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("movement"), w("act"))
        graph.add_parent_edge(w("act"), w("opera"))
        self.assertEqual(
            graph.ancestors(w("movement")), frozenset({w("act"), w("opera")}))

    def test_descendants_follows_children_downwards(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("movement"), w("act"))
        graph.add_parent_edge(w("act"), w("opera"))
        self.assertEqual(
            graph.descendants(w("opera")), frozenset({w("act"), w("movement")}))

    def test_hierarchy_ancestors_ignores_dropped_edges(self):
        """The hierarchy walk must not follow a cycle-closing edge."""
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("a"))
        graph.add_parent_edge(w("c"), w("b"))
        # The raw graph still lets a reach b and back to a.
        self.assertIn(w("b"), graph.ancestors(w("a")))
        # The hierarchy walk must terminate and stay consistent.
        result = graph.hierarchy_ancestors(w("a"))
        self.assertIn(w("b"), result)
        for work in result:
            self.assertNotEqual(
                work, w("a"),
                "hierarchy ancestors must not return the start work")

    def test_hierarchy_descendants_ignores_dropped_edges(self):
        """The hierarchy walk must not follow a cycle-closing edge.

        Here the cycle is broken by dropping the edge b -> a, so "a" is a
        child of "b" but not a descendant of itself.
        """
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("a"))
        graph.add_parent_edge(w("c"), w("b"))
        result = graph.hierarchy_descendants(w("b"))
        self.assertIn(w("c"), result)
        self.assertIn(w("a"), result)
        # The dropped edge b -> a means "a" has no child "b" below it.
        self.assertNotIn(w("b"), graph.hierarchy_descendants(w("a")))


class PropertyTestCase(unittest.TestCase):
    """Randomised checks of the properties the hierarchy relies on."""

    @staticmethod
    def _has_directed_cycle(edges):
        """True if the parent edges contain a hierarchy cycle.

        A cycle in the hierarchy is a *directed* cycle: a work's ancestor is
        also its descendant.
        """
        adjacency = {}
        for child, parent in edges:
            adjacency.setdefault(child, []).append(parent)
        colour = {}

        def visit(node):
            colour[node] = 1
            for nxt in adjacency.get(node, ()):
                if colour.get(nxt) == 1:
                    return True
                if colour.get(nxt, 0) == 0 and visit(nxt):
                    return True
            colour[node] = 2
            return False

        return any(
            colour.get(node, 0) == 0 and visit(node) for node in list(adjacency))

    def _random_graph(self, seed, size=7, density=0.28):
        rng = random.Random(seed)
        ids = [w(chr(ord("a") + i)) for i in range(size)]
        edges = []
        for child in ids:
            for parent in ids:
                if child != parent and rng.random() < density:
                    edges.append((child, parent))
        graph = WorkGraph()
        for child, parent in edges:
            graph.add_parent_edge(child, parent)
        return graph, edges

    def test_kept_edges_are_always_acyclic(self):
        for seed in range(200):
            graph, _ = self._random_graph(seed)
            kept = {
                (child, parent)
                for child, parents in graph.acyclic_parent_edges().items()
                for parent in parents
            }
            self.assertFalse(
                self._has_directed_cycle(kept),
                "cycle breaking left a cycle for seed %d" % seed)

    def test_no_droppable_edge_is_needlessly_lost(self):
        """The kept set must be maximal.

        Dropping more edges than necessary would disconnect works from their
        album and cost the user their tags, so every dropped edge must be
        genuinely required to break a cycle.
        """
        for seed in range(200):
            graph, original = self._random_graph(seed)
            kept = {
                (child, parent)
                for child, parents in graph.acyclic_parent_edges().items()
                for parent in parents
            }
            for edge in set(original) - kept:
                self.assertTrue(
                    self._has_directed_cycle(kept | {edge}),
                    "edge %r was dropped but could have been kept (seed %d)"
                    % (edge, seed))

    def test_result_is_independent_of_insertion_order(self):
        for seed in range(50):
            graph, original = self._random_graph(seed)
            rng = random.Random(seed + 1000)
            shuffled = list(original)
            rng.shuffle(shuffled)
            other = WorkGraph()
            for child, parent in shuffled:
                other.add_parent_edge(child, parent)
            first = {
                (child, parent)
                for child, parents in graph.acyclic_parent_edges().items()
                for parent in parents
            }
            second = {
                (child, parent)
                for child, parents in other.acyclic_parent_edges().items()
                for parent in parents
            }
            self.assertEqual(first, second, "order dependence for seed %d" % seed)


class FixtureGraphTestCase(unittest.TestCase):
    """The graph model applied to the real MusicBrainz fixtures.

    The fixtures are the recorded responses of the actual ``/ws/2/work``
    lookups, so they are the closest thing to production data available
    offline.
    """

    FIXTURE_ROOT = os.path.join(
        os.path.dirname(__file__), "fixtures")

    def _fixture_dirs(self):
        if not os.path.isdir(self.FIXTURE_ROOT):
            return []
        dirs = []
        for name in sorted(os.listdir(self.FIXTURE_ROOT)):
            path = os.path.join(self.FIXTURE_ROOT, name)
            if os.path.isdir(path) and glob.glob(os.path.join(path, "work_*.json")):
                dirs.append((name, path))
        return dirs

    def _build(self, fixdir):
        graph = WorkGraph()
        for path in glob.glob(os.path.join(fixdir, "work_*.json")):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            source = WorkId(data["id"])
            graph.add_work(source)
            for relation in data.get("relations", []):
                target = relation.get("work")
                if not target or not target.get("id"):
                    continue
                graph.add_relation(
                    source,
                    relation.get("type"),
                    relation.get("direction"),
                    WorkId(target["id"]))
        return graph

    def test_fixtures_are_available(self):
        self.assertTrue(self._fixture_dirs(), "no work fixtures found")

    def test_real_data_has_no_cycles_in_the_parent_relation(self):
        """MusicBrainz's hierarchical relation is acyclic in practice.

        The legacy implementation nevertheless had to guard against cycles,
        because its ``child_listing`` accumulated descendants across *all*
        relation types, so an arrangement or a "based on" link could make a
        legitimate parent look like a descendant.
        """
        for name, fixdir in self._fixture_dirs():
            graph = self._build(fixdir)
            graph.acyclic_parent_edges()
            self.assertEqual(
                graph.dropped_edges, [],
                "fixture %s produced dropped parent edges: %r"
                % (name, graph.dropped_edges))

    def test_non_hierarchical_relations_do_not_affect_the_hierarchy(self):
        """Only the relation types MusicBrainz intends may become parents.

        A work that has no ``parts`` relation may still be an arrangement of
        another work, and MusicBrainz treats that as a parent link, so the
        arrangement relations are followed. Every other relation type is
        purely informational and must not affect the hierarchy.
        """
        parent_types = {"parts", "arrangement", "medley"}
        for name, fixdir in self._fixture_dirs():
            graph = self._build(fixdir)
            acyclic = graph.acyclic_parent_edges()
            for source, relations in graph.other_edges.items():
                for relation_type, targets in relations.items():
                    if relation_type in parent_types:
                        continue
                    for target in targets:
                        self.assertNotIn(
                            target, acyclic.get(source, frozenset()),
                            "fixture %s: %s relation became a parent edge"
                            % (name, relation_type))

    def test_arrangement_is_not_followed_when_disabled(self):
        for name, fixdir in self._fixture_dirs():
            graph = self._build(fixdir)
            acyclic = graph.acyclic_parent_edges(use_arrangements=False)
            for source in graph.works():
                for relation in graph.relations_of(source, "arrangement", "backward"):
                    self.assertNotIn(
                        relation.target, acyclic.get(source, frozenset()),
                        "fixture %s: an arrangement became a parent edge"
                        % name)

    def test_hierarchy_is_order_independent_on_real_data(self):
        for name, fixdir in self._fixture_dirs():
            graph = self._build(fixdir)
            # Shuffle the relations themselves, not just the parent edges, so
            # that arrangements and medleys are carried over too.
            relations = list(graph.relations)
            rng = random.Random(4242)
            rng.shuffle(relations)
            other = WorkGraph()
            for relation in relations:
                other.add_relation(
                    relation.source,
                    relation.relation_type,
                    relation.direction,
                    relation.target,
                    relation.attributes)
            first = {
                (child, parent)
                for child, parents in graph.acyclic_parent_edges().items()
                for parent in parents
            }
            second = {
                (child, parent)
                for child, parents in other.acyclic_parent_edges().items()
                for parent in parents
            }
            self.assertEqual(
                first, second,
                "fixture %s: hierarchy depended on edge order" % name)
            self.assertEqual(
                graph.dropped_edges, other.dropped_edges,
                "fixture %s: the dropped edge depended on edge order" % name)

    def test_every_work_reaches_a_root(self):
        """No work may be stranded by the projection.

        A work that cannot reach a root has lost its place in the album and
        would silently lose all of its tags.
        """
        for name, fixdir in self._fixture_dirs():
            graph = self._build(fixdir)
            acyclic = graph.acyclic_parent_edges()
            for work in graph.works():
                seen = set()
                current = work
                while True:
                    parents = acyclic.get(current, frozenset())
                    if not parents:
                        break
                    nxt = min(parents)
                    if nxt in seen:
                        self.fail(
                            "fixture %s: %s is in a parent cycle"
                            % (name, work.mbid))
                    seen.add(nxt)
                    current = nxt


class PicardIndependenceTestCase(unittest.TestCase):
    """The hierarchy package must not depend on Picard.

    This is what allows the same model to serve the Picard 2 plugin, the
    Picard 3 plugin and the unit tests, and it is easy to lose by accident:
    one convenient ``from picard import log`` is enough to tie the whole
    package to a particular Picard version.
    """

    PACKAGE_ROOT = os.path.join(
        os.path.dirname(__file__), "..", "plugins", "classical_extras",
        "hierarchy")

    def _python_files(self):
        for dirpath, _dirnames, filenames in os.walk(self.PACKAGE_ROOT):
            if "__pycache__" in dirpath:
                continue
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    yield os.path.join(dirpath, filename)

    def test_package_has_no_picard_imports(self):
        offenders = []
        for path in self._python_files():
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if stripped.startswith(("import picard", "from picard")):
                        offenders.append("%s:%d: %s" % (path, number, stripped))
        self.assertEqual(
            offenders, [],
            "the hierarchy package must stay free of Picard imports:\n%s"
            % "\n".join(offenders))

    def test_package_imports_without_picard(self):
        """Import the package with Picard made unimportable."""
        import importlib

        saved = {
            name: module
            for name, module in sys.modules.items()
            if name == "picard" or name.startswith("picard.")
        }
        for name in saved:
            del sys.modules[name]

        blocker = _BlockPicardImports()
        sys.meta_path.insert(0, blocker)
        try:
            for name in list(sys.modules):
                if name.startswith("hierarchy"):
                    del sys.modules[name]
            module = importlib.import_module("hierarchy.graph")
            self.assertTrue(hasattr(module, "WorkGraph"))
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)


class _BlockPicardImports:
    """Meta path finder that refuses to import Picard."""

    def find_module(self, fullname, path=None):
        return self.find_spec(fullname, path)

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "picard" or fullname.startswith("picard."):
            raise ImportError("Picard must not be imported by the hierarchy "
                              "package (blocked: %s)" % fullname)
        return None


class ProjectionTestCase(unittest.TestCase):
    """Projection of a work graph into a hierarchy.

    Each work forms a node of its own. The node containing a work is the
    node holding the works that contain it, so a movement is contained by
    its act, which is contained by the opera.
    """

    def _graph(self, edges):
        graph = WorkGraph()
        for child, parent in edges:
            graph.add_parent_edge(child, parent)
        return graph

    def test_each_work_is_a_node_of_its_own(self):
        graph = self._graph([(w("movement"), w("opera"))])
        hierarchy = project(graph)
        self.assertEqual(
            hierarchy.node_of[w("movement")], NodeId(frozenset({w("movement")})))
        self.assertEqual(
            hierarchy.node_of[w("opera")], NodeId(frozenset({w("opera")})))

    def test_top_level_work_is_a_top_node(self):
        graph = self._graph([(w("movement"), w("opera"))])
        hierarchy = project(graph)
        self.assertEqual(hierarchy.tops, [NodeId(frozenset({w("opera")}))])

    def test_work_is_contained_by_the_node_holding_its_parents(self):
        graph = self._graph([
            (w("movement"), w("act")),
            (w("act"), w("opera")),
        ])
        hierarchy = project(graph)
        movement = hierarchy.node_of[w("movement")]
        # The node holding "act" contains the movement...
        container = NodeId(frozenset({w("act")}))
        self.assertEqual(hierarchy.nodes[movement].parents, frozenset({container}))
        # ...and the node holding "opera" contains that node.
        self.assertEqual(
            hierarchy.nodes[container].parents,
            frozenset({NodeId(frozenset({w("opera")}))}))

    def test_multi_parent_work_is_contained_by_a_fused_node(self):
        graph = self._graph([
            (w("arrangement"), w("akt1")),
            (w("arrangement"), w("akt3")),
        ])
        hierarchy = project(graph)
        node = hierarchy.nodes[hierarchy.node_of[w("arrangement")]]
        fused = NodeId(frozenset({w("akt1"), w("akt3")}))
        self.assertEqual(node.parents, frozenset({fused}))
        self.assertTrue(hierarchy.nodes[fused].is_fused)

    def test_node_identity_does_not_depend_on_parent_order(self):
        """The legacy tuple keys made ('a','b') and ('b','a') different nodes."""
        first = project(self._graph([
            (w("x"), w("a")), (w("x"), w("b"))]))
        second = project(self._graph([
            (w("x"), w("b")), (w("x"), w("a"))]))
        self.assertEqual(first.node_of[w("x")], second.node_of[w("x")])
        self.assertEqual(
            first.nodes[first.node_of[w("x")]].parents,
            second.nodes[second.node_of[w("x")]].parents)

    def test_siblings_share_a_container(self):
        graph = self._graph([
            (w("m1"), w("act")),
            (w("m2"), w("act")),
        ])
        hierarchy = project(graph)
        container = NodeId(frozenset({w("act")}))
        self.assertEqual(
            hierarchy.nodes[container].children,
            frozenset({hierarchy.node_of[w("m1")], hierarchy.node_of[w("m2")]}))

    def test_parents_and_children_are_linked(self):
        graph = self._graph([
            (w("movement"), w("act")),
            (w("act"), w("opera")),
        ])
        hierarchy = project(graph)
        act_node = NodeId(frozenset({w("act")}))
        opera_node = NodeId(frozenset({w("opera")}))
        movement_node = hierarchy.node_of[w("movement")]
        self.assertEqual(
            hierarchy.nodes[act_node].parents, frozenset({opera_node}))
        self.assertEqual(
            hierarchy.nodes[act_node].children, frozenset({movement_node}))

    def test_ancestors_and_descendants(self):
        graph = self._graph([
            (w("movement"), w("act")),
            (w("act"), w("opera")),
        ])
        hierarchy = project(graph)
        movement = hierarchy.node_of[w("movement")]
        act_node = NodeId(frozenset({w("act")}))
        opera_node = NodeId(frozenset({w("opera")}))
        self.assertEqual(
            hierarchy.ancestors(movement), frozenset({act_node, opera_node}))
        self.assertEqual(
            hierarchy.descendants(opera_node), frozenset({act_node, movement}))

    def test_every_work_reaches_a_top(self):
        graph = self._graph([
            (w("movement"), w("act")),
            (w("act"), w("opera")),
        ])
        hierarchy = project(graph)
        for work in graph.works():
            node = hierarchy.node_of[work]
            self.assertTrue(
                node in hierarchy.tops
                or hierarchy.ancestors(node) & set(hierarchy.tops),
                "%s cannot reach a top level node" % work.mbid)

    def test_cycle_does_not_appear_in_the_projection(self):
        graph = self._graph([
            (w("a"), w("b")),
            (w("b"), w("c")),
            (w("c"), w("a")),
        ])
        hierarchy = project(graph)
        for node in hierarchy.nodes.values():
            self.assertNotIn(node.id, node.parents)
            self.assertNotIn(node.id, node.children)

    def test_projection_is_independent_of_edge_order(self):
        edges = [
            (w("movement"), w("act")),
            (w("act"), w("opera")),
            (w("arr"), w("act")),
            (w("arr"), w("akt3")),
            (w("akt3"), w("opera")),
        ]
        reference = project(self._graph(edges))
        for seed in range(8):
            rng = random.Random(seed)
            shuffled = list(edges)
            rng.shuffle(shuffled)
            other = project(self._graph(shuffled))
            self.assertEqual(
                set(reference.node_of.items()), set(other.node_of.items()),
                "projection depended on edge order (seed %d)" % seed)
            self.assertEqual(reference.tops, other.tops)

    def test_path_to_top_is_the_shortest_one(self):
        graph = self._graph([
            (w("movement"), w("act")),
            (w("act"), w("opera")),
            (w("movement"), w("direct")),
            (w("direct"), w("opera")),
        ])
        hierarchy = project(graph)
        path = hierarchy.path_to_top(hierarchy.node_of[w("movement")])
        # The movement is contained by the node holding its parents, so the
        # shortest path to a top level node goes straight to the node holding
        # both "act" and "direct".
        self.assertEqual(
            path[-1], NodeId(frozenset({w("act"), w("direct")})))
        self.assertEqual(len(path), 2)

    def test_path_to_top_is_deterministic(self):
        edges = [
            (w("movement"), w("b")),
            (w("movement"), w("a")),
            (w("a"), w("opera")),
            (w("b"), w("opera")),
        ]
        hierarchy = project(self._graph(edges))
        first = hierarchy.path_to_top(hierarchy.node_of[w("movement")])
        for seed in range(5):
            rng = random.Random(seed)
            shuffled = list(edges)
            rng.shuffle(shuffled)
            other = project(self._graph(shuffled))
            self.assertEqual(
                first, other.path_to_top(other.node_of[w("movement")]))


class ParentResolutionTestCase(unittest.TestCase):
    """How a work's parent is found among MusicBrainz's relation types."""

    def test_parts_relation_is_the_parent(self):
        graph = WorkGraph()
        graph.add_relation(w("movement"), "parts", "backward", w("opera"))
        self.assertEqual(
            graph.resolve_parents()[w("movement")], frozenset({w("opera")}))

    def test_arrangement_is_used_when_there_is_no_parts_relation(self):
        graph = WorkGraph()
        graph.add_relation(
            w("arrangement"), "arrangement", "backward", w("opera"))
        self.assertEqual(
            graph.resolve_parents()[w("arrangement")], frozenset({w("opera")}))

    def test_arrangement_is_ignored_when_disabled(self):
        graph = WorkGraph()
        graph.add_relation(
            w("arrangement"), "arrangement", "backward", w("opera"))
        self.assertEqual(
            graph.resolve_parents(use_arrangements=False)[w("arrangement")],
            frozenset())

    def test_parts_wins_over_arrangement(self):
        """MusicBrainz intends a work with both to be treated as a part."""
        graph = WorkGraph()
        graph.add_relation(w("work"), "parts", "backward", w("part"))
        graph.add_relation(w("work"), "arrangement", "backward", w("opera"))
        self.assertEqual(
            graph.resolve_parents()[w("work")], frozenset({w("part")}))

    def test_medley_is_used_as_a_last_resort(self):
        graph = WorkGraph()
        graph.add_relation(w("medley"), "medley", "forward", w("opera"))
        self.assertEqual(
            graph.resolve_parents()[w("medley")], frozenset({w("opera")}))

    def test_medley_is_ignored_when_disabled(self):
        graph = WorkGraph()
        graph.add_relation(w("medley"), "medley", "forward", w("opera"))
        self.assertEqual(
            graph.resolve_parents(use_medleys=False)[w("medley")], frozenset())

    def test_collection_parent_is_ignored_when_collections_are_disabled(self):
        graph = WorkGraph()
        graph.add_relation(
            w("movement"), "parts", "backward", w("collection"),
            attributes=["part of collection"])
        self.assertEqual(
            graph.resolve_parents(use_collections=False)[w("movement")],
            frozenset())
        self.assertEqual(
            graph.resolve_parents(use_collections=True)[w("movement")],
            frozenset({w("collection")}))


class RedundantParentTestCase(unittest.TestCase):
    """Dropping a parent that is redundant given the others."""

    def test_ancestor_parent_is_dropped(self):
        """The broad grouping work is redundant next to the specific one."""
        graph = WorkGraph()
        graph.add_parent_edge(w("specific"), w("grouping"))
        graph.add_parent_edge(w("movement"), w("specific"))
        graph.add_parent_edge(w("movement"), w("grouping"))
        reduced = reduce_redundant_parents(
            graph, w("movement"), frozenset({w("specific"), w("grouping")}))
        self.assertEqual(reduced, frozenset({w("specific")}))

    def test_sibling_parents_are_both_kept(self):
        """Two versions of a suite are siblings, not ancestor and descendant."""
        graph = WorkGraph()
        graph.add_parent_edge(w("m1"), w("versionA"))
        graph.add_parent_edge(w("m2"), w("versionB"))
        graph.add_parent_edge(w("movement"), w("versionA"))
        graph.add_parent_edge(w("movement"), w("versionB"))
        reduced = reduce_redundant_parents(
            graph, w("movement"), frozenset({w("versionA"), w("versionB")}))
        self.assertEqual(reduced, frozenset({w("versionA"), w("versionB")}))

    def test_standalone_parent_is_dropped_for_an_embedded_one(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("embedded"), w("opera"))
        graph.add_parent_edge(w("movement"), w("embedded"))
        graph.add_parent_edge(w("movement"), w("standalone"))
        reduced = reduce_redundant_parents(
            graph, w("movement"), frozenset({w("embedded"), w("standalone")}))
        self.assertEqual(reduced, frozenset({w("embedded")}))

    def test_single_parent_is_never_reduced(self):
        graph = WorkGraph()
        graph.add_parent_edge(w("movement"), w("opera"))
        parents = frozenset({w("opera")})
        self.assertEqual(
            reduce_redundant_parents(graph, w("movement"), parents), parents)

    def test_reduction_never_empties_the_set(self):
        """A mutual ancestry cycle must not leave the work with no parent."""
        graph = WorkGraph()
        graph.add_parent_edge(w("a"), w("b"))
        graph.add_parent_edge(w("b"), w("a"))
        reduced = reduce_redundant_parents(
            graph, w("a"), frozenset({w("a"), w("b")}))
        self.assertTrue(reduced)


if __name__ == "__main__":
    unittest.main()
