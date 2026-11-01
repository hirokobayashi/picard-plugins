#!/usr/bin/env python
# coding: utf-8
"""Tests for the adapter between MusicBrainz's JSON and the model.

The adapter is the seam between the wire format and the work-hierarchy
model. It reads the responses the web service returns and writes the
hierarchy tags the plugin publishes, so it has to reproduce two things
the model deliberately does not care about:

* the *order* of the ids inside a level, which the legacy took from the
  order the relations came back in;
* the *format* of a level's ids, which the legacy wrote as the repr of
  a tuple.

These tests pin both, and check the adapter against the recorded
fixtures. They need no Picard.
"""

import glob
import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "plugins", "classical_extras"))

from hierarchy.adapter import (  # noqa: E402
    AlbumAdapter,
    WorkNames,
    WorkRelations,
    format_ids,
    node_height,
    read_performance,
    read_work_graph,
)
from hierarchy.album import chains_of  # noqa: E402
from hierarchy.graph import NodeId, WorkId  # noqa: E402
from hierarchy.project import Hierarchy  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def w(mbid):
    """A work id."""
    return WorkId(mbid)


def _node(mbid):
    """A node id."""
    return NodeId(frozenset([w(mbid)]))


class _FakeNode:
    """A node of a hand-built hierarchy."""

    def __init__(self, node_id, children):
        self.id = node_id
        self.works = node_id.works
        self.children = frozenset(children)
        self.parents = frozenset()


def _responses(album):
    """The work and recording responses of a fixture album."""
    directory = os.path.join(FIXTURES, album)
    works = [
        _load(path)
        for path in sorted(glob.glob(os.path.join(directory, "work_*.json")))
    ]
    recordings = [
        _load(path)
        for path in sorted(glob.glob(os.path.join(directory, "rec_*.json")))
    ]
    return works, recordings


def _load(path):
    """One recorded response."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _names(works):
    """The titles of the given work responses."""
    return WorkNames(titles={w(item["id"]): item.get("title", "")
                             for item in works})


#: Whether each fixture album is tagged with the plugin's ``cwp_partial``
#: option on. The option decides whether a work that is only partly
#: performed is represented by the recording id or by the work itself,
#: and each album's legacy test sets its own value.
USE_PARTIAL = {
    "daphnis": True,
    "firebird": True,
    "gayne": False,
    "nutcracker": True,
    "planets": False,
    "requiem": True,
    "swanlake_full": True,
    "swanlake_jarvi": True,
    "tristan": False,
}


def _adapter(album, use_partial=None):
    """An adapter over a fixture album."""
    if use_partial is None:
        use_partial = USE_PARTIAL[album]
    works, recordings = _responses(album)
    return AlbumAdapter(
        works, recordings, names=_names(works), use_partial=use_partial)


def _recording(work_id, recording_id, partial=False, attributes=None):
    """A recording that performs one work."""
    relation = {
        "type": "performance",
        "direction": "forward",
        "target-type": "work",
        "work": {"id": work_id, "title": ""},
        "attributes": list(attributes or ()),
    }
    if partial:
        relation["attributes"] = ["partial"]
    return {
        "id": recording_id,
        "title": "",
        "relations": [relation],
    }


class FormatIdsTestCase(unittest.TestCase):
    """The format of a level's ids."""

    def test_one_id_is_written_as_a_one_element_tuple(self):
        self.assertEqual(
            format_ids([w("abc")]),
            "('abc',)")

    def test_two_ids_keep_their_order(self):
        self.assertEqual(
            format_ids([w("abc"), w("def")]),
            "('abc', 'def')")

    def test_the_order_is_the_one_given(self):
        self.assertEqual(
            format_ids([w("def"), w("abc")]),
            "('def', 'abc')")

    def test_no_ids_is_an_empty_tuple(self):
        self.assertEqual(format_ids([]), "()")


class ReadPerformanceTestCase(unittest.TestCase):
    """Reading a recording's performance relations."""

    def test_a_plain_performance(self):
        performance = read_performance(
            _recording("work-1", "rec-1"))
        self.assertEqual(performance.key, w("rec-1"))
        self.assertEqual(performance.works, frozenset([w("work-1")]))
        self.assertEqual(performance.complete, frozenset([w("work-1")]))
        self.assertEqual(performance.partial, frozenset())

    def test_a_partial_performance(self):
        performance = read_performance(
            _recording("work-1", "rec-1", partial=True))
        self.assertEqual(performance.partial, frozenset([w("work-1")]))
        self.assertEqual(performance.complete, frozenset())

    def test_the_partial_attribute_is_ignored_when_the_option_is_off(self):
        performance = read_performance(
            _recording("work-1", "rec-1", partial=True),
            use_partial=False)
        self.assertEqual(performance.partial, frozenset())
        self.assertEqual(performance.complete, frozenset([w("work-1")]))

    def test_a_work_related_twice_is_performed_in_full_as_well(self):
        """A work related once partially and once in full is in both sets.

        The recording stands in for the work, and the work still
        contributes - it is the parent of the recording id one level up.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-1"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-1"},
                    "attributes": [],
                },
            ],
        }
        performance = read_performance(recording)
        self.assertEqual(performance.works, frozenset([w("work-1")]))
        self.assertEqual(performance.partial, frozenset([w("work-1")]))
        self.assertEqual(performance.complete, frozenset([w("work-1")]))

    def test_other_relations_are_ignored(self):
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-1"},
                },
                {
                    "type": "arrangement",
                    "direction": "forward",
                    "work": {"id": "work-2"},
                },
            ],
        }
        performance = read_performance(recording)
        self.assertEqual(performance.works, frozenset([w("work-1")]))


class RecordingOrderTestCase(unittest.TestCase):
    """The order of a recording's first level.

    The legacy's node keys are tuples, and the order inside a tuple is
    the order the relations came back in.
    """

    def _members(self, recording, use_partial=True):
        adapter = AlbumAdapter(
            [], [recording], use_partial=use_partial)
        return adapter.recordings[0].members

    def test_the_relation_order_is_kept(self):
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        self.assertEqual(
            self._members(recording),
            [w("work-b"), w("work-a")])

    def test_a_partial_work_is_replaced_by_the_recording_id(self):
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                    "attributes": ["partial"],
                },
            ],
        }
        self.assertEqual(
            self._members(recording),
            [w("work-b"), w("rec-1")])

    def test_a_partial_work_is_kept_when_the_option_is_off(self):
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                    "attributes": ["partial"],
                },
            ],
        }
        self.assertEqual(
            self._members(recording, use_partial=False),
            [w("work-a")])

    def test_a_work_related_twice_is_held_once(self):
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        self.assertEqual(self._members(recording), [w("rec-1")])


class AdapterFixtureTestCase(unittest.TestCase):
    """The adapter over the recorded MusicBrainz fixtures."""

    def test_daphnis_the_group_is_one_name_for_the_whole_album(self):
        tags = _adapter("daphnis").tags()
        groups = {track.group for track in tags.values()}
        self.assertEqual(groups, {"Daphnis et Chloé"})

    def test_daphnis_every_track_has_a_chain(self):
        tags = _adapter("daphnis").tags()
        self.assertEqual(len(tags), 19)
        for track in tags.values():
            self.assertTrue(track.levels)

    def test_firebird_the_partial_work_is_replaced_by_the_recording_id(self):
        """The recording stands in for the work it only partly performs."""
        tags = _adapter("firebird").tags()
        recording = w("036a332c-4e01-4e74-8528-456060d37182")
        track = tags[recording]
        self.assertEqual(
            track.levels[0],
            [w("a06c71e7-e293-4206-a40b-a4cf89b34cb0"), recording])

    def test_firebird_the_other_tracks_hold_one_work(self):
        tags = _adapter("firebird").tags()
        recording = w("036a332c-4e01-4e74-8528-456060d37182")
        for key, track in tags.items():
            if key == recording:
                continue
            self.assertEqual(len(track.levels[0]), 1, key.mbid)

    def test_the_ids_are_written_as_a_tuple_repr(self):
        tags = _adapter("daphnis").tags()
        recording = w("03e53458-39f8-482a-b470-80a5a70d15bf")
        metadata = tags[recording].as_metadata()
        self.assertEqual(
            metadata["~cwp_workid_0"],
            "('03e53458-39f8-482a-b470-80a5a70d15bf',)")

    def test_the_level_count_is_the_distance_to_the_top(self):
        tags = _adapter("daphnis").tags()
        recording = w("03e53458-39f8-482a-b470-80a5a70d15bf")
        track = tags[recording]
        self.assertEqual(len(track.levels), 4)
        self.assertEqual(track.part_levels, 3)

    def test_the_top_is_the_last_level(self):
        tags = _adapter("daphnis").tags()
        recording = w("03e53458-39f8-482a-b470-80a5a70d15bf")
        track = tags[recording]
        metadata = track.as_metadata()
        self.assertEqual(metadata["~cwp_workid_top"], track.id_string(-1))

    def test_the_group_is_always_one_value(self):
        """The group is one string, and it is one of the album's groups."""
        for album, groups in (
                ("daphnis", {"Daphnis et Chloé"}),
                ("gayne", {"Gayaneh, op. 50"}),
                ("swanlake_full", {"Swan Lake, op. 20"}),
                ("tristan", {
                    "Tristan und Isolde, WWV 90",
                    "Rienzi, der letzte der Tribunen, WWV 49",
                    "Lohengrin, WWV 75",
                    "Der Ring des Nibelungen, WWV 86",
                    "Wesendonck-Lieder",
                    "Die Meistersinger von Nürnberg, WWV 96",
                })):
            for track in _adapter(album).tags().values():
                self.assertIsInstance(track.group, str, album)
                self.assertIn(track.group, groups, album)

    def test_planets_the_group_is_the_vote_winner(self):
        """Every track of the suite carries the same group."""
        tags = _adapter("planets").tags()
        groups = {}
        for track in tags.values():
            groups.setdefault(track.group, 0)
            groups[track.group] += 1
        self.assertEqual(
            groups["The Planets, op. 32"], 7)
        self.assertEqual(
            groups["The Planets Suite extension"], 1)

    def test_the_top_may_hold_several_works(self):
        """The model keeps both Swan Lake editions in the top node."""
        tags = _adapter("swanlake_full").tags()
        tops = {
            track.id_string(-1)
            for track in tags.values()
        }
        self.assertTrue(
            any(len(top.split(", ")) > 1 for top in tops),
            tops)

    def test_every_fixture_release_produces_tags(self):
        for album in (
                "daphnis", "firebird", "gayne", "nutcracker", "planets",
                "requiem", "swanlake_full", "swanlake_jarvi", "tristan"):
            tags = _adapter(album).tags()
            self.assertTrue(tags, album)
            for track in tags.values():
                self.assertTrue(track.levels, album)
                self.assertTrue(track.group, album)


class ReadWorkGraphTestCase(unittest.TestCase):
    """Reading the work responses into a graph."""

    def test_a_work_is_added_to_the_graph(self):
        graph, relations = read_work_graph([
            {"id": "work-1", "title": "A work"}])
        self.assertIn(w("work-1"), set(graph.works()))

    def test_a_parent_relation_becomes_a_parent_edge(self):
        graph, relations = read_work_graph([
            {
                "id": "work-1",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "work-2"},
                    },
                ],
            },
        ])
        self.assertEqual(
            relations.parents[w("work-1")],
            [w("work-2")])

    def test_the_parent_order_is_the_relation_order(self):
        graph, relations = read_work_graph([
            {
                "id": "work-1",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "work-b"},
                    },
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "work-a"},
                    },
                ],
            },
        ])
        self.assertEqual(
            relations.order_of([w("work-a"), w("work-b")]),
            [w("work-b"), w("work-a")])

    def test_a_forward_relation_is_not_a_parent(self):
        graph, relations = read_work_graph([
            {
                "id": "work-1",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "forward",
                        "work": {"id": "work-2"},
                    },
                ],
            },
        ])
        self.assertNotIn(w("work-1"), relations.parents)

    def test_an_unknown_work_is_ordered_by_its_id(self):
        """A work no relation names keeps the result deterministic."""
        graph, relations = read_work_graph([])
        self.assertEqual(
            relations.order_of([w("work-b"), w("work-a")]),
            [w("work-a"), w("work-b")])


class ArrangementOrderTestCase(unittest.TestCase):
    """The order of a level reached through an arrangement relation.

    The Tristan album's "Prelude and Liebestod" has no ``parts`` parent
    at all - it reaches the opera through an ``arrangement`` relation.
    The legacy wrote that level's ids in the order the arrangement
    relation gave them, which is not the work id order.
    """

    def test_the_arrangement_order_is_kept(self):
        works = [
            {
                "id": "work-1",
                "title": "The work",
                "relations": [
                    {
                        "type": "arrangement",
                        "direction": "backward",
                        "work": {"id": "work-b"},
                    },
                    {
                        "type": "arrangement",
                        "direction": "backward",
                        "work": {"id": "work-a"},
                    },
                ],
            },
        ]
        graph, relations = read_work_graph(works)
        self.assertEqual(
            relations.parents[w("work-1")],
            [w("work-b"), w("work-a")])
        self.assertEqual(
            relations.order_of([w("work-a"), w("work-b")], through=w("work-1")),
            [w("work-b"), w("work-a")])

    def test_the_parts_relation_wins_over_the_arrangement(self):
        works = [
            {
                "id": "work-1",
                "relations": [
                    {
                        "type": "arrangement",
                        "direction": "backward",
                        "work": {"id": "work-a"},
                    },
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
        ]
        graph, relations = read_work_graph(works)
        self.assertEqual(
            relations.parents[w("work-1")],
            [w("work-b")])

    def test_tristan_the_liebestod_reaches_its_acts_in_order(self):
        """The real fixture: the order is the arrangement's, not the id's."""
        tags = _adapter("tristan").tags()
        # find the track whose chain holds the two acts
        for track in tags.values():
            for level in track.levels:
                ids = [work.mbid for work in level]
                if any(i.startswith("eb691aba") for i in ids):
                    self.assertEqual(
                        [i[:8] for i in ids],
                        ["eb691aba", "52836ec3"],
                        "the arrangement relation's order")
                    return
        self.fail("no track reaches the two acts")


class TopNameTestCase(unittest.TestCase):
    """The name of a track's top work.

    Two works can share a title - the two editions of Swan Lake are both
    called "Swan Lake, op. 20" - and the tag then holds the name once.
    """

    def test_two_works_with_one_name_give_one_name(self):
        names = WorkNames(titles={
            w("work-a"): "Swan Lake, op. 20",
            w("work-b"): "Swan Lake, op. 20",
        })
        self.assertEqual(
            names.names_of([w("work-a"), w("work-b")]),
            ["Swan Lake, op. 20"])

    def test_two_different_names_give_two_names(self):
        names = WorkNames(titles={
            w("work-a"): "A work",
            w("work-b"): "Another work",
        })
        self.assertEqual(
            names.names_of([w("work-a"), w("work-b")]),
            ["A work", "Another work"])

    def test_the_swan_lake_top_is_named_once(self):
        tags = _adapter("swanlake_full").tags()
        for track in tags.values():
            for name in track.top_names:
                self.assertNotEqual(name, "", track.group)
            self.assertEqual(
                len(track.top_names),
                len(set(track.top_names)),
                track.group)

    def test_the_top_name_is_the_vote_winner(self):
        """The name the most tracks of the album voted for.

        The Planets album's track 7 has a top with two candidate names -
        the suite and its extension - and the name the rest of the album
        voted for is the one it gets.
        """
        tags = _adapter("planets").tags()
        fused = {
            track.top_names[0]
            for track in tags.values()
            if len(track.top) > 1
        }
        self.assertEqual(fused, {"The Planets, op. 32"})

    def test_the_fused_top_names_are_the_vote_winner(self):
        """A top with several candidates is named by the vote."""
        tags = _adapter("planets").tags()
        counts = {}
        for track in tags.values():
            for name in track.top_names:
                counts[name] = counts.get(name, 0) + 1
        for track in tags.values():
            for name in track.top_names:
                self.assertEqual(
                    counts[name],
                    max(counts[other] for other in counts
                        if other in track.top_names),
                    name)

    def test_every_track_sharing_a_top_names_it_the_same_way(self):
        """Tracks under the same top work carry the same top name.

        This is what the vote is for: the top work tag has to agree across
        the album, or a release's tracks would be tagged inconsistently.
        """
        for album in ("daphnis", "firebird", "gayne", "nutcracker",
                      "planets", "requiem", "swanlake_full",
                      "swanlake_jarvi", "tristan"):
            tags = _adapter(album).tags()
            by_top = {}
            for track in tags.values():
                by_top.setdefault(track.id_string(-1), set()).add(
                    tuple(track.top_names))
            for top, names in by_top.items():
                self.assertEqual(
                    len(names), 1,
                    "%s: the top %s is named %s" % (album, top, names))


class NodeHeightTestCase(unittest.TestCase):
    """The height of a node.

    The hierarchy is a tree, so a node is reached by one path - but the
    height has to be the longest path, not the first one found.
    """

    def _hierarchy(self, shape):
        """A hierarchy of the given shape.

        The shape is a dict of node id to the ids of its children.
        """
        nodes = {}
        for node_id, children in shape.items():
            nodes[node_id] = _FakeNode(node_id, children)
        return Hierarchy(
            nodes=nodes, node_of={}, node_of_performance={})

    def test_a_leaf_is_zero(self):
        hierarchy = self._hierarchy({_node("a"): []})
        self.assertEqual(node_height(hierarchy, _node("a")), 0)

    def test_a_chain_is_its_length(self):
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("c")],
            _node("c"): [],
        })
        self.assertEqual(node_height(hierarchy, _node("a")), 2)

    def test_a_diamond_takes_the_longest_path(self):
        """A node reachable by two paths takes the longer one.

        The tree is
        ::

            a
           / \\
          b   d
          |   |\\
          c   | e
          |   |
          +---+

        so ``d`` is reached both directly and through ``c``, and the
        longest path from ``a`` is a -> b -> c -> d -> e.
        """
        hierarchy = self._hierarchy({
            _node("a"): [_node("b"), _node("d")],
            _node("b"): [_node("c")],
            _node("c"): [_node("d")],
            _node("d"): [_node("e")],
            _node("e"): [],
        })
        self.assertEqual(node_height(hierarchy, _node("a")), 4)

    def test_the_height_of_a_subtree(self):
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("c")],
            _node("c"): [],
        })
        self.assertEqual(node_height(hierarchy, _node("b")), 1)

    def test_a_cycle_does_not_hang(self):
        """A cycle must not make the walk go on for ever."""
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("a")],
        })
        self.assertEqual(node_height(hierarchy, _node("a")), 2)


class ChainShapeTestCase(unittest.TestCase):
    """The shape of a track's chain.

    The adapter reads a chain positionally - each level hangs off the work
    the level below was reached by - so the chain has to hold no node
    twice, and the levels have to be in order.
    """

    def test_no_chain_holds_a_node_twice(self):
        """A node repeated in a chain would make the levels ambiguous."""
        for album in ("daphnis", "firebird", "gayne", "nutcracker",
                      "planets", "requiem", "swanlake_full",
                      "swanlake_jarvi", "tristan"):
            adapter = _adapter(album)
            hierarchy, _groups = adapter.build()
            for chain in chains_of(hierarchy, adapter.performances).values():
                ids = list(chain)
                self.assertEqual(
                    len(ids), len(set(ids)),
                    "%s: a chain holds a node twice" % album)

    def test_the_top_is_the_last_level(self):
        for album in ("daphnis", "gayne", "swanlake_full"):
            for track in _adapter(album).tags().values():
                self.assertTrue(track.levels, album)
                self.assertEqual(
                    track.id_string(-1),
                    track.id_string(len(track.levels) - 1),
                    album)


class OrderOfTestCase(unittest.TestCase):
    """The order of a level's works.

    A work no relation named has to sort after the ones that were named,
    whatever the work ids are.
    """

    def test_an_unnamed_work_sorts_last(self):
        relations = WorkRelations(
            parents={w("child"): [w("unrelated"), w("work-b")]})
        self.assertEqual(
            relations.order_of(
                [w("work-a"), w("work-b")], through=w("child")),
            [w("work-b"), w("work-a")])

    def test_an_unnamed_work_sorts_last_when_its_id_is_smaller(self):
        """The named work keeps its place even when its id sorts later.

        The parent list names ``bbb`` at position 1 and an unrelated work
        at position 0. The unnamed ``aaa`` has to sort after it, not
        before it - the position in the relation is what counts, not the
        work id.
        """
        relations = WorkRelations(
            parents={w("child"): [w("unrelated"), w("bbb")]})
        self.assertEqual(
            relations.order_of(
                [w("aaa"), w("bbb")], through=w("child")),
            [w("bbb"), w("aaa")])


class AdapterWithoutPicardTestCase(unittest.TestCase):
    """The adapter needs no Picard."""

    def test_the_module_does_not_import_picard(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "plugins", "classical_extras",
            "hierarchy", "adapter.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("import picard", source)
        self.assertNotIn("from picard", source)


if __name__ == "__main__":
    unittest.main()
