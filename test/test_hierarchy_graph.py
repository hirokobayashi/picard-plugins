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


#: How many random graphs the property tests check.
#:
#: The cycle breaking is verified against a brute force reference on this
#: many random graphs, which is what backs the claim that the kept edge set
#: is always acyclic and always maximal.
_PROPERTY_SEEDS = 5000


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
        for seed in range(_PROPERTY_SEEDS):
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
        for seed in range(_PROPERTY_SEEDS):
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
        for seed in range(_PROPERTY_SEEDS // 4):
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
                    WorkId(target["id"]),
                    relation.get("attributes") or [])
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

    def test_tristan_track_tops_match_the_legacy(self):
        """The projection must put every track under the same top work.

        The legacy pipeline was run over this fixture and its
        ``chosen_top`` mapping was recorded: which work each track is
        tagged with. Pinning that mapping per track, rather than just the
        set of top works, is what makes the check meaningful -- a change
        that re-heads one track under a different top work is caught even
        when every top work still appears somewhere on the album.

        The top work of a track is the work at the top of the hierarchy
        above it.
        """
        fixdir = os.path.join(self.FIXTURE_ROOT, "tristan")
        if not os.path.isdir(fixdir):
            self.skipTest("no tristan fixture")
        graph = self._build(fixdir)
        hierarchy = project(graph)

        # The legacy's chosen top for each track, recorded from a run of the
        # legacy pipeline over this fixture.
        expected = {
            "t1": "315a0fe2-4e5a-477f-a4b7-e9c66523b70e",  # Rienzi
            "t2": "ae217ba8-0b07-4b0b-aed6-c80535dcd94b",  # Tristan und Isolde
            "t3": "1d774427-c5f5-4bc9-9fd6-040f8ff4381c",  # Lohengrin
            "t4": "1d774427-c5f5-4bc9-9fd6-040f8ff4381c",
            "t5": "cb3cd3bb-b471-46e9-94dc-6cd04ea94414",  # Wesendonck-Lieder
            "t6": "cb3cd3bb-b471-46e9-94dc-6cd04ea94414",
            "t7": "cb3cd3bb-b471-46e9-94dc-6cd04ea94414",
            "t8": "cb3cd3bb-b471-46e9-94dc-6cd04ea94414",
            "t9": "cb3cd3bb-b471-46e9-94dc-6cd04ea94414",
            "t10": "4d644732-9876-4b0d-9c2c-b6a738d6530e",  # Meistersinger
            "t11": "597e017a-2c97-40c0-9eeb-430a0461e4ad",  # Der Ring
        }

        actual = {}
        for path in sorted(glob.glob(os.path.join(fixdir, "rec_*.json"))):
            label = os.path.basename(path)[len("rec_"):-len(".json")]
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            work = None
            for relation in data.get("relations", []):
                target = relation.get("work")
                if target and target.get("id"):
                    work = WorkId(target["id"])
                    break
            self.assertIsNotNone(work, "%s points at no work" % label)
            node = hierarchy.node_of.get(work)
            self.assertIsNotNone(
                node, "%s: the work is missing from the hierarchy" % label)
            top = hierarchy.path_to_top(node)
            self.assertTrue(top, "%s: the work has no path to a top" % label)
            actual[label] = {member.mbid for member in top[-1].works}

        self.assertEqual(
            actual,
            {label: {top} for label, top in expected.items()})

    def test_the_scene_level_is_kept(self):
        """The Gayne movements keep the scene they belong to.

        The movements of "Act I, Scene I (Spring)" are listed under both
        the scene and the act. The scene is itself a part of the act, so
        the act is an ancestor of the scene and the reduction drops the
        ancestor - the scene, which is the more specific placement, is
        kept.

        The legacy flattens these to the act, because it never resolved
        the scene's own chain and so could not see the scene sits under
        the act. The scene is where the movements really live, so the
        extra level is kept.
        """
        fixdir = os.path.join(self.FIXTURE_ROOT, "gayne")
        if not os.path.isdir(fixdir):
            self.skipTest("no gayne fixture")
        hierarchy = project(self._build(fixdir))

        scene = w("697f1f18-1a3c-4981-85ee-eb2c43e9c971")
        act = w("9ffd930d-eb52-4c0a-8ee4-551a5d7c32ad")
        ballet = w("693e681e-98a1-4d21-9bc8-e084a248461d")
        movement = w("fb479c25-21be-4fba-aaa5-78693fcaafdd")

        # The movement's chain reaches the ballet through the scene and
        # the act, both of them. The exact chain is asserted, not just the
        # presence of each work: a chain that fused the scene with the act
        # would still contain both, but the scene level would be gone.
        path = hierarchy.path_to_top(hierarchy.node_of[movement])
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in path],
            [[movement.mbid], [scene.mbid], [act.mbid], [ballet.mbid]],
            "the movement's chain is not movement, scene, act, ballet")

    def test_every_node_has_at_most_one_parent(self):
        """The hierarchy is a tree, so every node has at most one parent.

        A node that held several parents would make the chain to a track
        ambiguous: the track would sit under more than one level at once.
        The projection merges the parents of a work into a single node
        precisely to avoid that.
        """
        for name in sorted(os.listdir(self.FIXTURE_ROOT)):
            fixdir = os.path.join(self.FIXTURE_ROOT, name)
            if not os.path.isdir(fixdir):
                continue
            if not glob.glob(os.path.join(fixdir, "work_*.json")):
                continue
            hierarchy = project(self._build(fixdir))
            for node_id, node in hierarchy.nodes.items():
                self.assertLessEqual(
                    len(node.parents), 1,
                    "%s: node %s has %d parents"
                    % (name, node_id, len(node.parents)))
                # The children must be the mirror of the parents: a node
                # that is a child of two nodes is no longer a tree.
                for child in node.children:
                    self.assertIn(
                        node_id, hierarchy.nodes[child].parents,
                        "%s: node %s is a child of %s but does not list it"
                        % (name, child, node_id))

    def test_the_tree_keeps_both_branches_of_a_fused_node(self):
        """The tree is wider than the chain to any one track.

        On the Tristan album the movement "Prelude and Liebestod" is reached
        through the Akt I Vorspiel and through the Akt III Liebestod. The
        node holding those two works is a fused node, and the tree keeps both
        branches: "Akt I" is a node under the opera, and "Akt I, Vorspiel" is
        a node of its own whose parent is "Akt I".

        Only the chain to the movement goes through one branch.
        """
        fixdir = os.path.join(self.FIXTURE_ROOT, "tristan")
        if not os.path.isdir(fixdir):
            self.skipTest("no tristan fixture")
        graph = self._build(fixdir)
        hierarchy = project(graph)

        akt1 = w("30a37617-1426-4f1d-be7d-12af1790590e")
        vorspiel = w("52836ec3-2272-37cd-a4af-20ab3bf56b88")
        opera = w("ae217ba8-0b07-4b0b-aed6-c80535dcd94b")

        # "Akt I, Vorspiel" is a node of its own, and it hangs off "Akt I".
        vorspiel_node = hierarchy.node_of[vorspiel]
        self.assertEqual(
            hierarchy.nodes[vorspiel_node].parents,
            frozenset({hierarchy.node_of[akt1]}),
            "the Akt I branch is no longer attached to the Akt I node")

        # "Akt I" is a child of the opera.
        self.assertIn(
            hierarchy.node_of[akt1],
            hierarchy.nodes[hierarchy.node_of[opera]].children,
            "the Akt I branch is no longer under the opera")

        # The movement's chain reaches the opera through one branch only,
        # and the fused node is on it.
        movement = w("7b4b0ef2-7928-3c37-8107-1355eb043855")
        path = hierarchy.path_to_top(hierarchy.node_of[movement])
        self.assertTrue(
            any(vorspiel in node.works for node in path),
            "the fused node is not on the chain to the movement")

    def test_a_work_appears_at_most_once_in_a_chain(self):
        """A chain must not repeat a work.

        On the Tristan album "Prelude and Liebestod" is reached by two
        routes: through the Akt I Vorspiel and through the Akt III
        Liebestod. The two routes run at different depths, so the container
        of that work held both of them as parents. Keeping both put the
        opera twice in one chain, so the track was tagged with a level that
        repeated the level above it.

        This checks the real fixture rather than a synthetic shape, because
        the shape that reproduces it is not obvious: the work level
        reduction already handles the simpler cases.
        """
        fixdir = os.path.join(self.FIXTURE_ROOT, "tristan")
        if not os.path.isdir(fixdir):
            self.skipTest("no tristan fixture")
        graph = self._build(fixdir)
        hierarchy = project(graph)
        movement = w("7b4b0ef2-7928-3c37-8107-1355eb043855")
        self.assertIn(movement, hierarchy.node_of)
        path = hierarchy.path_to_top(hierarchy.node_of[movement])
        works = [work for node in path for work in node.works]
        self.assertEqual(
            len(works), len(set(works)),
            "a work appears twice in the chain %r" % (path,))
        # The chain reaches the opera, once.
        self.assertEqual(
            [work.mbid for work in path[-1].works],
            ["ae217ba8-0b07-4b0b-aed6-c80535dcd94b"])

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
        self.assertEqual(path[-1], hierarchy.node_of[w("opera")])
        self.assertIn(path[-1], hierarchy.tops)
        self.assertEqual(len(path), 3)

    def test_work_reachable_by_two_routes_still_reaches_the_top(self):
        """A work under a fused container must not be lost.

        The container of a work with two parents holds both of them, and is
        itself contained by the node holding their parents. If that link is
        missing the container becomes an orphan root and everything below it
        is invisible from the top of the tree, which is the "the track lost
        its top work" defect.
        """
        graph = self._graph([
            (w("movement"), w("act")),
            (w("movement"), w("direct")),
            (w("act"), w("opera")),
            (w("direct"), w("opera")),
        ])
        hierarchy = project(graph)
        movement = hierarchy.node_of[w("movement")]
        self.assertIn(
            hierarchy.node_of[w("opera")], hierarchy.ancestors(movement))
        path = hierarchy.path_to_top(movement)
        self.assertEqual(path[-1], hierarchy.node_of[w("opera")])
        self.assertIn(path[-1], hierarchy.tops)
        self.assertIn(
            movement, hierarchy.descendants(hierarchy.node_of[w("opera")]))

    def test_siblings_stay_at_the_same_level(self):
        """A work with a redundant parent must not sit flatter than its
        siblings.

        Without the reduction the work below both the specific and the broad
        parent is contained by a node holding both, one level higher than a
        sibling that only has the specific parent.
        """
        graph = self._graph([
            (w("specific"), w("grouping")),
            (w("m1"), w("specific")),
            (w("m1"), w("grouping")),
            (w("m2"), w("specific")),
        ])
        hierarchy = project(graph)

        def container_of(work):
            return hierarchy.nodes[hierarchy.node_of[work]].parents

        self.assertEqual(
            container_of(w("m1")), container_of(w("m2")))

    def test_reduction_can_be_switched_off(self):
        graph = self._graph([
            (w("specific"), w("grouping")),
            (w("m1"), w("specific")),
            (w("m1"), w("grouping")),
            (w("m2"), w("specific")),
        ])
        hierarchy = project(graph, reduce_redundant=False)

        def container_of(work):
            return hierarchy.nodes[hierarchy.node_of[work]].parents

        self.assertNotEqual(
            container_of(w("m1")), container_of(w("m2")))

    def test_the_flatter_branch_is_dropped(self):
        """The parent that reaches the shared work in fewer steps is dropped.

        A work reached by two routes has a container holding both routes.
        When one route reaches the shared work in a single step and the other
        reaches it through a longer chain, the single step route is the
        flatter of the two and adds nothing, so it is dropped. Keeping both
        would leave the shared work at two levels of the chain.
        """
        graph = self._graph([
            (w("movement"), w("viaAct1")),
            (w("movement"), w("viaScene3")),
            (w("viaAct1"), w("act1")),
            (w("act1"), w("opera")),
            (w("viaScene3"), w("scene3")),
            (w("scene3"), w("act3")),
            (w("act3"), w("opera")),
        ])
        hierarchy = project(graph)
        path = hierarchy.path_to_top(hierarchy.node_of[w("movement")])
        levels = [sorted(work.mbid for work in node.works) for node in path]
        self.assertEqual(
            levels,
            [["movement"], ["viaAct1", "viaScene3"], ["scene3"], ["act3"],
             ["opera"]])

    def test_a_parent_that_carries_its_own_branch_is_kept(self):
        """A parent is not dropped when it leads somewhere the other cannot.

        A parent can have several parents of its own, one of which the other
        parent also reaches and one of which only it reaches. Dropping it
        would lose the branch that only it leads to, so the rule that drops
        the flatter of two parents must not fire here.
        """
        graph = self._graph([
            (w("movement"), w("branchA")),
            (w("movement"), w("branchB")),
            # branchA leads to both tops.
            (w("branchA"), w("top1")),
            (w("branchA"), w("top2")),
            # branchB reaches top1 through a longer chain.
            (w("branchB"), w("mid")),
            (w("mid"), w("top1")),
        ])
        hierarchy = project(graph)
        # The movement is filed under both branches, so the chain keeps the
        # one that leads to top2.
        movement = hierarchy.node_of[w("movement")]
        container = next(iter(hierarchy.nodes[movement].parents))
        self.assertEqual(
            sorted(work.mbid for work in container.works),
            ["branchA", "branchB"])
        # top2 is only reachable through branchA.
        tops = {work.mbid for node in hierarchy.tops for work in node.works}
        self.assertIn("top2", tops)

    def test_parents_reaching_a_shared_work_at_the_same_depth_are_kept(self):
        """Neither parent is dropped when both reach a shared work.

        Two parents can reach the same work by chains of the same length.
        Neither is then the flatter of the two, so neither is dropped. The
        rule that drops the flatter branch must check that the converse does
        not hold, otherwise one of them would be dropped even though it is
        no flatter than the other.
        """
        graph = self._graph([
            (w("work"), w("branchA")),
            (w("work"), w("branchB")),
            # branchA also has a parent branchB cannot reach, so the "all"
            # check already spares branchA; only the symmetry check spares
            # branchB.
            (w("branchA"), w("shared")),
            (w("branchA"), w("other")),
            (w("branchB"), w("shared")),
        ])
        hierarchy = project(graph)
        work = hierarchy.node_of[w("work")]
        container = next(iter(hierarchy.nodes[work].parents))
        self.assertEqual(
            sorted(work.mbid for work in container.works),
            ["branchA", "branchB"],
            "a parent was dropped even though both reach the shared work")

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

    def test_the_parents_are_the_same_whatever_the_options(self):
        """The parents of each work, for every combination of options.

        The expected parents are written out by hand, so the test does
        not work the expectation out of the code it is checking.

        The graph has a collection relation and a backward medley, so
        that the filters are exercised and not just switched on.

        The collection relation is on a work of its own, so that the
        filter has something to filter - a work with an ordinary
        relation as well would keep that relation whatever the option
        says.
        """
        graph = WorkGraph()
        graph.add_relation(w("movement"), "parts", "backward", w("opera"))
        graph.add_relation(w("movement"), "parts", "backward", w("act"))
        graph.add_relation(w("arrangement"), "arrangement", "backward",
                           w("opera"))
        graph.add_relation(w("medley"), "medley", "forward", w("opera"))
        graph.add_relation(w("medley"), "medley", "backward", w("backwards"))
        graph.add_relation(
            w("collection"), "parts", "backward", w("box"),
            attributes=("part of collection",))
        # the parents of each work, for every combination of the three
        # options. The movement has parts parents, so they are used and
        # the arrangement and medley fallbacks do not come into it.
        for use_arrangements in (True, False):
            for use_medleys in (True, False):
                for use_collections in (True, False):
                    label = "%s %s %s" % (
                        use_arrangements, use_medleys, use_collections)
                    sets = graph.resolve_parents(
                        use_arrangements=use_arrangements,
                        use_medleys=use_medleys,
                        use_collections=use_collections)
                    ordered = graph.resolve_parents_ordered(
                        use_arrangements=use_arrangements,
                        use_medleys=use_medleys,
                        use_collections=use_collections)
                    # the movement has two parts parents
                    self.assertEqual(
                        sets[w("movement")],
                        frozenset({w("opera"), w("act")}),
                        label)
                    # the arrangement has no parts parent, so the
                    # arrangement relation is used - unless arrangements
                    # are off
                    self.assertEqual(
                        sets[w("arrangement")],
                        frozenset({w("opera")}) if use_arrangements
                        else frozenset(),
                        label)
                    # the medley has no parts or arrangement parent, so
                    # the medley relation is used - unless medleys are
                    # off. The backward one is not a parent.
                    self.assertEqual(
                        sets[w("medley")],
                        frozenset({w("opera")}) if use_medleys
                        else frozenset(),
                        label)
                    # the collection's parent is dropped when
                    # collections are off
                    self.assertEqual(
                        sets[w("collection")],
                        frozenset({w("box")}) if use_collections
                        else frozenset(),
                        label)
                    # and the ordered resolution holds the same parents
                    self.assertEqual(
                        {work: frozenset(parents)
                         for work, parents in ordered.items()},
                        sets,
                        label)

    def test_the_collection_filter_is_exercised(self):
        """A collection parent is dropped when collections are off.

        Without this, the test above would pass even if the filter were
        gone - there would be no collection edge to filter.
        """
        graph = WorkGraph()
        graph.add_relation(
            w("collection"), "parts", "backward", w("box"),
            attributes=("part of collection",))
        self.assertEqual(
            graph.resolve_parents()[w("collection")], frozenset({w("box")}))
        self.assertEqual(
            graph.resolve_parents(use_collections=False)[w("collection")],
            frozenset())

    def test_a_backward_medley_is_not_a_parent(self):
        """A backward medley relation is not a parent.

        The medley fallback only takes a forward relation.
        """
        graph = WorkGraph()
        graph.add_relation(w("medley"), "medley", "backward", w("opera"))
        self.assertEqual(
            graph.resolve_parents()[w("medley")], frozenset())

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
