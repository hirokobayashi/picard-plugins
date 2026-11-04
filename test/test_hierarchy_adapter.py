#!/usr/bin/env python
# coding: utf-8
"""Tests for the adapter between MusicBrainz's JSON and the model.

The adapter is the seam between the wire format and the work-hierarchy
model. It reads the responses the web service returns and writes the
hierarchy tags the plugin publishes, so it has to reproduce two things
the model deliberately does not care about:

* the *order* of the ids inside a level, which the legacy took from the
  order the relations came back in;
* the *format* of a level's ids, which the legacy handed to Picard as a
  tuple - Picard stores a tuple of one id as a bare string and a tuple
  of more as a list, and the adapter hands Picard the same tuple.

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
    TrackTags,
    WorkNames,
    WorkRelations,
    has_cycle,
    heights_are_reliable,
    heights_of,
    node_height,
    read_performance,
    read_work_graph,
)
from hierarchy.album import candidate_tops, chains_of  # noqa: E402
from hierarchy.graph import NodeId, WorkId  # noqa: E402
from hierarchy.project import Hierarchy  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PLUGIN_DIR = os.path.join(
    os.path.dirname(__file__), "..", "plugins", "classical_extras")


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


def _recording_id(album, label):
    """The recording id of a fixture track.

    The label is the fixture's own name for the track - "d1t1" and so on.
    """
    path = os.path.join(
        FIXTURES, album, "rec_%s.json" % label)
    return _load(path)["id"]


def _first_relation_is_partial(relations, work):
    """Whether the *first* relation to the work is a partial one.

    The legacy reads the attributes of the first relation it finds for
    a work, so a work related in full first is a work performed in full
    - whatever a later relation says.
    """
    for relation in relations:
        if relation["work"]["id"] != work:
            continue
        return 'partial' in (relation.get("attributes") or ())
    return False


def _as_picard_stores(value):
    """The tag as Picard stores it.

    Picard stores a single value as a bare string and more than one as a
    list, so a tuple of one id reads as a bare id and a tuple of two
    reads as a list of two.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int) or isinstance(value, float):
        return value
    values = [str(item) for item in value]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _starts_with(stored, legacy):
    """The stored tag holds the legacy's ids first, in the same order.

    The legacy's tag may be a bare id, a list, or the semicolon joined
    string Picard writes a list out as - so the ids are read out of
    whichever it is.
    """
    if legacy is None:
        return True
    legacy_ids = _split_ids(legacy)
    stored_ids = _split_ids(stored)
    if len(stored_ids) < len(legacy_ids):
        return False
    return stored_ids[:len(legacy_ids)] == legacy_ids


def _split_ids(value):
    """The ids of a stored tag, however it is stored."""
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(
            part.strip() for part in value.split(';') if part.strip())
    return tuple(str(item) for item in value)


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
        The partial relation comes first here.
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

    def test_a_work_related_twice_in_the_other_order(self):
        """A work related in full first is performed in full.

        The first relation decides: the legacy reads the attributes of
        the first relation it finds for the work, so a work related in
        full first and partly second is a work performed in full - the
        recording id does not stand in for it, and it is only in the
        complete set.

        The legacy writes ~cwp_workid_0 = ('work-1', 'work-a') for such
        a recording - the work itself, not the recording id.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-1"},
                    "attributes": [],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-1"},
                    "attributes": ["partial"],
                },
            ],
        }
        performance = read_performance(recording)
        self.assertEqual(performance.works, frozenset([w("work-1")]))
        self.assertEqual(performance.partial, frozenset())
        self.assertEqual(performance.complete, frozenset([w("work-1")]))

    def test_a_work_related_twice_both_partly_is_only_partial(self):
        """A work related twice, both partly, is only partly performed.

        The recording never performs it in full, so it is only in the
        partial set - it does not contribute itself to the level above.
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
                    "attributes": ["partial"],
                },
            ],
        }
        performance = read_performance(recording)
        self.assertEqual(performance.works, frozenset([w("work-1")]))
        self.assertEqual(performance.partial, frozenset([w("work-1")]))
        self.assertEqual(performance.complete, frozenset())

    def test_a_work_related_three_times_partly_partly_and_then_in_full(self):
        """A work related partly, partly and then in full is in both sets.

        The first relation decides whether the recording stands in for
        the work - and it is a partial one, so it does. A relation in
        full afterwards still puts the work in the complete set, so the
        work contributes itself one level up.

        The legacy was run on this case with
        ``test/tools/legacy_probe.py`` and it writes the work one level
        up, so this is what the legacy does.
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

    def test_the_level_0_order_is_the_relation_order(self):
        """The recording's first level is in the relation order.

        The legacy's ids are written in the order the relations came back
        in, not in work id order.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "zzz-work"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "aaa-work"},
                },
            ],
        }
        adapter = AlbumAdapter([], [recording])
        self.assertEqual(
            adapter.recordings[0].members,
            [w("zzz-work"), w("aaa-work")])

    def test_a_work_related_twice_is_held_once(self):
        """A work related twice is held once."""
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

    def test_the_ids_are_written_as_a_tuple(self):
        """The id tags hold a tuple, as the legacy's do.

        Picard stores a tuple of one id as a bare string and a tuple of
        more as a list - so this is what the legacy's own tags read as.
        """
        tags = _adapter("daphnis").tags()
        recording = w("03e53458-39f8-482a-b470-80a5a70d15bf")
        metadata = tags[recording].as_metadata()
        self.assertEqual(
            metadata["~cwp_workid_0"],
            ("03e53458-39f8-482a-b470-80a5a70d15bf",))

    def test_the_level_count_is_the_distance_to_the_top(self):
        tags = _adapter("daphnis").tags()
        recording = w("03e53458-39f8-482a-b470-80a5a70d15bf")
        track = tags[recording]
        self.assertEqual(len(track.levels), 4)
        self.assertEqual(track.part_levels, 3)

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
        self.assertTrue(
            any(len(track.top) > 1 for track in tags.values()),
            "no track has a top holding several works")

    def test_every_fixture_release_produces_tags(self):
        """Every release gives every track a hierarchy.

        The ids themselves are checked against the legacy's own tags by
        ``LegacyParityTestCase``.
        """
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

    def test_a_set_of_contributing_works_gives_the_same_order(self):
        """A set of contributing works gives the same order every time.

        A set has no order of its own, so the works are put in work id
        order first - otherwise the result would differ from one run to
        the next. A set's order is fixed for the life of a process, so
        the check is made in a few processes with different hash seeds.

        The two works name different parents, so the order of the works
        is the order of the parents. The parents are named so that their
        id order is the *opposite* of the order the works give - so
        that the order cannot come from the ids either.
        """
        import subprocess
        import sys
        script = (
            "import os, sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "from hierarchy.adapter import read_work_graph\n"
            "from hierarchy.graph import WorkId\n"
            "graph, relations = read_work_graph([\n"
            "    {'id': 'work-a', 'relations': [\n"
            "        {'type': 'parts', 'direction': 'backward',\n"
            "         'work': {'id': 'parent-zzz'}}]},\n"
            "    {'id': 'work-b', 'relations': [\n"
            "        {'type': 'parts', 'direction': 'backward',\n"
            "         'work': {'id': 'parent-aaa'}}]},\n"
            "    {'id': 'parent-aaa', 'title': 'The parent of B'},\n"
            "    {'id': 'parent-zzz', 'title': 'The parent of A'},\n"
            "])\n"
            "wanted = [WorkId('parent-aaa'), WorkId('parent-zzz')]\n"
            "print(','.join(\n"
            "    work.mbid for work in relations.order_of(\n"
            "        wanted, through={WorkId('work-a'), WorkId('work-b')})))\n"
        ) % (PLUGIN_DIR, os.path.dirname(os.path.abspath(__file__)))
        results = set()
        for seed in ("0", "1", "2", "12345"):
            environment = dict(os.environ)
            environment['PYTHONHASHSEED'] = seed
            finished = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, env=environment,
                cwd=os.path.dirname(os.path.abspath(__file__)))
            self.assertEqual(
                finished.returncode, 0, finished.stderr)
            results.add(finished.stdout.strip())
        self.assertEqual(
            results, {"parent-zzz,parent-aaa"},
            "the order differs between the processes")


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
        """The real fixture: the order is the arrangement's, not the id's.

        Track 2 of the Tristan album is the one that reaches both acts.
        """
        tags = _adapter("tristan").tags()
        track = tags[WorkId(_recording_id("tristan", "t2"))]
        found = [
            [work.mbid[:8] for work in level]
            for level in track.levels
            if len(level) > 1
        ]
        self.assertIn(["eb691aba", "52836ec3"], found)


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

    def test_a_tied_top_names_every_tied_name(self):
        """A tie keeps every tied name, in the order they came in.

        No fixture has a tie - the two Swan Lake editions share one
        title, so the names are one. So the tie is built here.

        The names are not in the order they would be sorted in, so that
        an implementation that sorted them would not pass. The legacy
        keeps the order the relations gave: its own two functions were
        run on this case.
        """
        adapter = _adapter("planets")
        names = WorkNames(titles={
            w("work-a"): "The second work",
            w("work-b"): "The first work",
        })
        adapter.names = names
        # a top of two works, with one vote each
        votes = {"The first work": 1, "The second work": 1}
        top_names = adapter._top_names([w("work-a"), w("work-b")], votes)
        self.assertEqual(
            top_names, ["The second work", "The first work"])

    def test_a_tied_top_is_written_as_a_list(self):
        """The names of a tied top are written as a list.

        The legacy hands Picard the list of the tied names, so Picard
        stores a list of them. The adapter hands Picard a tuple, which
        Picard stores the same way - a tuple of two names is stored as a
        list of two, and a tuple of one as a bare name.
        """
        adapter = _adapter("planets")
        adapter.names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        votes = {"The first work": 1, "The second work": 1}
        track = TrackTags(
            key=w("rec-1"),
            levels=[[w("work-a"), w("work-b")]],
            top=[w("work-a"), w("work-b")],
            top_names=adapter._top_names(
                [w("work-a"), w("work-b")], votes))
        self.assertEqual(
            track.as_metadata()['~cwp_work_top'],
            ("The first work", "The second work"))
        # Picard stores a tuple and a list the same way, so the two are
        # the same as far as the tag file is concerned
        self.assertEqual(
            _as_picard_stores(track.as_metadata()['~cwp_work_top']),
            _as_picard_stores(["The first work", "The second work"]))

    def test_a_top_with_no_votes_keeps_every_name(self):
        """Votes that name none of the candidates keep every name.

        Every name is tied at zero votes, so they are all kept - which is
        what the legacy does.

        The legacy has this fallback for a caller outside
        ``process_album``. Through the tags it cannot happen - a track's
        own candidates are voted for - so it is checked here.
        """
        adapter = _adapter("planets")
        adapter.names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        votes = {"Another work": 3}
        top_names = adapter._top_names([w("work-a"), w("work-b")], votes)
        self.assertEqual(
            top_names, ["The first work", "The second work"])

    def test_a_top_with_no_votes_at_all_keeps_the_first(self):
        """With no votes at all the first name is kept.

        This is the legacy's fallback when there is no vote information -
        for a caller outside ``process_album``. Through the tags it
        cannot happen, so it is checked here.
        """
        adapter = _adapter("planets")
        adapter.names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        top_names = adapter._top_names([w("work-a"), w("work-b")], {})
        self.assertEqual(top_names, ["The first work"])

    def test_the_swan_lake_top_is_named_once(self):
        """Two editions with one title give that title once.

        Both Swan Lake editions are called "Swan Lake, op. 20", so the
        name appears once. The vote is exercised by the Planets album,
        where the two candidate names differ.
        """
        tags = _adapter("swanlake_full").tags()
        for track in tags.values():
            self.assertEqual(
                track.top_names,
                ["Swan Lake, op. 20"],
                track.group)

    def test_the_fused_top_names_are_the_vote_winner(self):
        """A top with several candidates is named by the vote.

        Planets' track 7 has a top holding two works - the suite and its
        extension - and their names are "The Planets, op. 32" and "The
        Planets Suite extension". The name the rest of the album voted
        for is the one it gets.
        """
        tags = _adapter("planets").tags()
        fused = [
            track for track in tags.values()
            if len(track.top) > 1
        ]
        self.assertEqual(len(fused), 1, "expected one fused top")
        self.assertEqual(
            fused[0].top_names,
            ["The Planets, op. 32"])

    def test_the_vote_counts_the_candidate_names(self):
        """A name is counted once per track that has it as a candidate."""
        adapter = _adapter("planets")
        hierarchy, _groups = adapter.build()
        candidates = candidate_tops(
            chains_of(hierarchy, adapter.performances), hierarchy)
        counts = adapter._votes(candidates)
        self.assertEqual(counts["The Planets, op. 32"], 7)
        self.assertEqual(counts["The Planets Suite extension"], 2)

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
                by_top.setdefault(
                    tuple(work.mbid for work in track.top), set()).add(
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

    def test_a_node_the_hierarchy_does_not_hold_raises(self):
        """A node the hierarchy does not hold has no height.

        Zero is the height of a leaf, so a caller that passed a stale or
        mistyped node would get a plausible-looking answer.
        """
        hierarchy = self._hierarchy({_node("a"): []})
        self.assertRaises(KeyError, node_height, hierarchy, _node("b"))

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

        The children are held in a set, so the order they are visited in
        is not fixed - and a height that stopped at the first path
        found would give the right answer only when it happened to
        visit the longer path first. So the tree is walked in a few
        processes with different hash seeds.
        """
        import subprocess
        script = (
            "import os, sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "from hierarchy.adapter import node_height\n"
            "from hierarchy.graph import NodeId, WorkId\n"
            "class Fake:\n"
            "    def __init__(self, node_id, children):\n"
            "        self.id = node_id\n"
            "        self.works = node_id.works\n"
            "        self.children = frozenset(children)\n"
            "        self.parents = frozenset()\n"
            "def node(name):\n"
            "    return NodeId(frozenset([WorkId(name)]))\n"
            "nodes = {}\n"
            "for name, children in (\n"
            "        ('a', ['b', 'd']),\n"
            "        ('b', ['c']),\n"
            "        ('c', ['d']),\n"
            "        ('d', ['e']),\n"
            "        ('e', []),\n"
            "):\n"
            "    nodes[node(name)] = Fake(\n"
            "        node(name), [node(child) for child in children])\n"
            "from hierarchy.adapter import Hierarchy\n"
            "hierarchy = Hierarchy(\n"
            "    nodes=nodes, node_of={}, node_of_performance={})\n"
            "print(node_height(hierarchy, node('a')))\n"
        ) % (PLUGIN_DIR, os.path.dirname(os.path.abspath(__file__)))
        results = set()
        for seed in ("0", "1", "2", "12345"):
            environment = dict(os.environ)
            environment['PYTHONHASHSEED'] = seed
            finished = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, env=environment,
                cwd=os.path.dirname(os.path.abspath(__file__)))
            self.assertEqual(
                finished.returncode, 0, finished.stderr)
            results.add(finished.stdout.strip())
        self.assertEqual(
            results, {"4"},
            "the height differs between the processes")

    def test_the_height_of_a_subtree(self):
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("c")],
            _node("c"): [],
        })
        self.assertEqual(node_height(hierarchy, _node("b")), 1)

    def test_a_cycle_does_not_hang(self):
        """A cycle must not make the walk go on for ever.

        The node on the path is left out of the walk, so it finishes.
        The height it gives is not to be relied on, so it is not given -
        the caller is told that the walk was not reliable.
        """
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("a")],
        })
        self.assertTrue(has_cycle(hierarchy, _node("a")))
        self.assertRaises(
            ValueError, node_height, hierarchy, _node("a"))

    def test_a_cycle_is_reported(self):
        """A cycle is reported, so the caller can leave the track alone.

        The height of a walk that found a cycle is not to be relied on,
        so a caller that knows about the cycle can leave the track with
        no tags rather than write a wrong depth.
        """
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("a")],
        })
        self.assertTrue(has_cycle(hierarchy, _node("a")))

    def test_a_tree_is_not_reported_as_a_cycle(self):
        """A tree has no cycle - not even a shared child."""
        hierarchy = self._hierarchy({
            _node("a"): [_node("b"), _node("d")],
            _node("b"): [_node("c")],
            _node("c"): [_node("d")],
            _node("d"): [_node("e")],
            _node("e"): [],
        })
        self.assertFalse(has_cycle(hierarchy, _node("a")))

    def test_the_heights_are_reliable_only_for_a_whole_tree(self):
        """The heights can be relied on only when the tree is whole.

        A cycle stops the walk and a child that is not there is counted
        as a leaf - so the depth is wrong either way, and the caller has
        to know.
        """
        whole = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [],
        })
        self.assertTrue(heights_are_reliable(whole, _node("a")))
        cyclic = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("a")],
        })
        self.assertFalse(heights_are_reliable(cyclic, _node("a")))
        broken = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("c")],
        })
        self.assertFalse(heights_are_reliable(broken, _node("a")))

    def test_the_heights_of_a_node_that_is_not_there_are_not_reliable(self):
        """The heights of a node the hierarchy does not hold are not
        reliable.

        The node has no height at all - ``node_height`` raises for it -
        so the walk must not say that the heights can be relied on.
        """
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [],
        })
        self.assertFalse(heights_are_reliable(hierarchy, _node("c")))
        self.assertFalse(has_cycle(hierarchy, _node("c")))

    def test_the_heights_of_an_unreliable_tree_are_not_given(self):
        """The heights of a tree that cannot be relied on are not given.

        A cycle or a child that is not there makes the depth wrong, and
        a plausible-looking wrong height is worse than none.
        """
        cyclic = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("a")],
        })
        self.assertRaises(
            ValueError, heights_of, cyclic, _node("a"))
        broken = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("c")],
        })
        self.assertRaises(
            ValueError, heights_of, broken, _node("a"))
        # a whole tree gives its heights
        whole = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [],
        })
        self.assertEqual(
            heights_of(whole, _node("a")), {_node("a"): 1, _node("b"): 0})

    def test_a_self_loop_does_not_hang(self):
        """A self loop must not make the walk go on for ever."""
        hierarchy = self._hierarchy({
            _node("a"): [_node("a")],
        })
        self.assertTrue(has_cycle(hierarchy, _node("a")))
        self.assertRaises(
            ValueError, node_height, hierarchy, _node("a"))


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
        """The top work's ids are the ids of the last level.

        The expected top is worked out from the candidate tops, which is
        derived from the chain independently of the adapter's own tag
        building. And the top is the last level - so the two have to
        agree.
        """
        for album in ("daphnis", "gayne", "swanlake_full"):
            adapter = _adapter(album)
            hierarchy, _groups = adapter.build()
            candidates = candidate_tops(
                chains_of(hierarchy, adapter.performances), hierarchy)
            for track in adapter.tags().values():
                self.assertTrue(track.levels, album)
                self.assertEqual(
                    set(track.top),
                    set(candidates[track.key]),
                    album)
                self.assertEqual(
                    list(track.top),
                    list(track.levels[-1]),
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

    def test_the_work_ids_are_ordered_by_their_id(self):
        """The work ids are ordered by their id.

        The sorts of the work ids say which key they use, so that they
        do not depend on the work id class being ordered. The class
        itself is ordered by its id - it has one field - so the two
        agree.
        """
        self.assertEqual(
            sorted([w("bbb"), w("aaa"), w("ccc")]),
            [w("aaa"), w("bbb"), w("ccc")])

    def test_works_given_as_plain_strings_are_work_ids(self):
        """Works given as plain strings are work ids.

        The one that gives the order takes a bare string, so the works
        do too.
        """
        relations = WorkRelations(
            parents={w("child"): [w("unrelated"), w("work-b")]})
        self.assertEqual(
            relations.order_of(["work-a", "work-b"], through=w("child")),
            [w("work-b"), w("work-a")])
        # a bare string is one work, not a list of characters
        self.assertEqual(
            relations.order_of("work-b", through=w("child")),
            [w("work-b")])

    def test_a_bare_work_id_is_taken_as_a_work(self):
        """A plain string is a work id, not a list of them.

        A work id is not a string - it is a dataclass - so a bare string
        is a work id and not a list of one.
        """
        relations = WorkRelations(
            parents={w("child"): [w("unrelated"), w("work-b")]})
        self.assertEqual(
            relations.order_of(
                [w("work-a"), w("work-b")], through="child"),
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

    def test_every_contributing_work_is_read(self):
        """Two works leading to the same level both contribute.

        A recording that performs two works puts them in one node, and
        that node holds both of their parents. Each contributing work's
        own relation gives the order of the parents it names.
        """
        relations = WorkRelations(parents={
            w("work-a"): [w("parent-b"), w("parent-a")],
            w("work-b"): [w("parent-a")],
        })
        self.assertEqual(
            relations.order_of(
                [w("parent-a"), w("parent-b")],
                through=[w("work-a"), w("work-b")]),
            [w("parent-b"), w("parent-a")])

    def test_the_first_contributing_work_wins(self):
        """Two contributing works naming a parent each.

        Each names its parent first, so the positions collide. The work
        that comes first has to win, not the one whose id sorts first.
        """
        relations = WorkRelations(parents={
            w("work-a"): [w("zzz-parent")],
            w("work-b"): [w("aaa-parent")],
        })
        self.assertEqual(
            relations.order_of(
                [w("aaa-parent"), w("zzz-parent")],
                through=[w("work-a"), w("work-b")]),
            [w("zzz-parent"), w("aaa-parent")])

    def test_the_contributing_works_are_in_the_level_below_order(self):
        """The works leading to a level keep the order of the level below.

        The order is the point: the contributing works are the level
        below's works whose parents are in this level, and they are in
        the level below's order. The set is worked out from the same
        relations the adapter reads, so this pins the order rather than
        the rule - the rule itself is checked against the legacy's own
        tags by ``LegacyParityTestCase``.
        """
        adapter = _adapter("swanlake_full")
        hierarchy, _groups = adapter.build()
        chains = chains_of(hierarchy, adapter.performances)
        checked = 0
        for recording in adapter.recordings:
            chain = chains.get(recording.performance.key) or []
            levels, through = adapter._levels(recording, chain)
            for index in range(2, len(levels)):
                leaders = through[index]
                if len(leaders) < 2:
                    continue
                # the contributing works are the level below's works whose
                # parents are in this level
                below = levels[index - 1]
                above = chain[index]
                expected = [
                    work for work in below
                    if adapter.relations.parents.get(work)
                    and set(adapter.relations.parents[work]) & set(above.works)]
                checked += 1
                self.assertEqual(
                    set(leaders), set(expected),
                    "the contributing works are not the right ones")
                # and they are in the level below's order
                self.assertEqual(
                    leaders,
                    [work for work in below if work in expected],
                    "the contributing works are not in the level's order")
        self.assertTrue(checked, "no level had two contributing works")


class PartialOrderTestCase(unittest.TestCase):
    """The order of the level above a partly performed work.

    A partly performed work is represented by the recording id in the
    level below, so the level below cannot say where it stood. It takes
    the recording id's place, which is where the relation put it.
    """

    def _adapter(self):
        """A recording that partly performs two works.

        One work is performed in part and in full, so it contributes
        itself to the level above - the recording id stands in for it one
        level below. The other is performed in part only, so it
        contributes nothing.
        """
        works = [
            {
                "id": "work-zzz",
                "title": "A work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-1"},
                    },
                ],
            },
            {
                "id": "work-aaa",
                "title": "Another work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-2"},
                    },
                ],
            },
            {"id": "parent-1", "title": "The first parent"},
            {"id": "parent-2", "title": "The second parent"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-zzz"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-zzz"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-aaa"},
                    "attributes": ["partial"],
                },
            ],
        }]
        return AlbumAdapter(works, recordings, use_partial=True)

    def test_a_partial_only_work_contributes_nothing(self):
        """A work performed only in part contributes no parent.

        The recording also performs a work in full, and that work's
        parents are the parent of the recording's node - so the partly
        performed work is not there.

        The legacy was run on this case with
        ``test/tools/legacy_probe.py``: it writes the same levels.
        """
        tags = self._adapter().tags()
        track = tags[w("rec-1")]
        self.assertEqual(
            track.levels[2],
            [w("parent-1")])

    def test_the_recording_id_stands_in_for_the_work(self):
        """The level below holds the recording id, not the work."""
        tags = self._adapter().tags()
        track = tags[w("rec-1")]
        self.assertEqual(track.levels[0], [w("rec-1")])
        self.assertEqual(track.levels[1], [w("work-zzz")])

    def test_a_partial_only_work_is_not_in_the_chain(self):
        """A work performed only in part is in the recording's chain
        nowhere.

        The recording also performs a work in full, and that work's
        parents are the parent of the recording's node - so the partly
        performed work is not there.

        The legacy was run on this case with
        ``test/tools/legacy_probe.py``: it writes the same levels.
        """
        adapter = self._adapter()
        hierarchy, _groups = adapter.build()
        chains = chains_of(hierarchy, adapter.performances)
        for recording in adapter.recordings:
            chain = chains.get(recording.performance.key) or []
            for node in chain:
                for work in node.works:
                    self.assertNotEqual(
                        work, w("work-aaa"),
                        "the partial-only work is in the chain")

    def test_a_partial_only_work_alone_is_the_parent(self):
        """A recording that performs only a partly performed work has
        that work above it.

        There is no work performed in full, so nothing else decides the
        parent - and the partly performed work is it. The recording id
        stands in for the work one level below it.

        The legacy was run on this case with
        ``test/tools/legacy_probe.py``: it writes the recording id, the
        work and the work's parent.
        """
        works = [
            {
                "id": "work-a",
                "title": "A work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-1"},
                    },
                ],
            },
            {"id": "parent-1", "title": "The first parent"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                    "attributes": ["partial"],
                },
            ],
        }]
        tags = AlbumAdapter(works, recordings, use_partial=True).tags()
        track = tags[w("rec-1")]
        self.assertEqual(track.levels[0], [w("rec-1")])
        self.assertEqual(track.levels[1], [w("work-a")])
        self.assertEqual(track.levels[2], [w("parent-1")])

    def test_two_partial_works_keep_the_relation_order(self):
        """The level above two works holds their parents, in order.

        A level's ids are the parents of the works below it, in the order
        those works' own relations gave them. So the level above the two
        works holds each work's parent, and the order is the order the
        works' relations give.

        The two works are both performed in part, so the recording id
        stands in for both of them in the first level - and the level
        above holds the works themselves, in the order the relations gave
        them. The legacy writes the same: workid_0 = ('rec-1',) and
        workid_1 = ('work-zzz', 'work-aaa').
        """
        works = [
            {
                "id": "work-zzz",
                "title": "A work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-1"},
                    },
                ],
            },
            {
                "id": "work-aaa",
                "title": "Another work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-2"},
                    },
                ],
            },
            {"id": "parent-1", "title": "The first parent"},
            {"id": "parent-2", "title": "The second parent"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-zzz"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-zzz"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-aaa"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-aaa"},
                },
            ],
        }]
        adapter = AlbumAdapter(works, recordings, use_partial=True)
        tags = adapter.tags()
        track = tags[w("rec-1")]
        # the first level holds the recording id alone - it stands in for
        # both of the works
        self.assertEqual(track.levels[0], [w("rec-1")])
        # and the level above holds the two works, in the order the
        # relations gave them - not the work id order
        self.assertEqual(
            track.levels[1],
            [w("work-zzz"), w("work-aaa")])
        # and the level above that holds their parents, one each, in the
        # same order
        self.assertEqual(
            track.levels[2],
            [w("parent-1"), w("parent-2")])
        # and the two works themselves are in the order the relations gave
        # them - the recording id stands in for them in the first level,
        # so the relations are what say
        self.assertEqual(
            adapter.recordings[0].performed,
            [w("work-zzz"), w("work-aaa")])


class PartialOrderFixtureTestCase(unittest.TestCase):
    """A partly performed work's place among the others.

    The expectations are the legacy's own: the fixture was run through
    the legacy and the tags it wrote are recorded here.
    """

    #: The legacy's tags for the fixture, with the recording naming
    #: work-b first. It wrote workid_0 = ('rec-1', 'work-a') and
    #: workid_1 = ('work-b',).
    #:
    #: The legacy's work lookups did not reach the top of this fixture,
    #: so its workid_top is one level short of the model's - the levels
    #: below are the ones compared here.
    LEGACY_FIRST_B = {
        "~cwp_workid_0": ("rec-1", "work-a"),
        "~cwp_workid_1": ("work-b",),
    }
    #: The legacy's tags with the recording naming work-a first. It wrote
    #: workid_0 = ('work-a', 'rec-1') and workid_1 = ('work-b',).
    LEGACY_FIRST_A = {
        "~cwp_workid_0": ("work-a", "rec-1"),
        "~cwp_workid_1": ("work-b",),
    }

    #: The model's own tags for the fixture. The legacy's work lookups
    #: did not reach the top of this fixture, so its workid_top is one
    #: level short of the model's - and the levels above the first are
    #: the model's own decision. They are recorded here so that a
    #: change in them would be caught.
    MODEL = {
        "~cwp_part_levels": "3",
        "~cwp_work_part_levels": "3",
        "~cwp_workid_2": ("parent-b",),
        "~cwp_workid_3": ("top",),
        "~cwp_workid_top": ("top",),
    }

    def test_the_model_writes_the_levels_above_the_first(self):
        """The levels above the first are the model's own, and recorded.

        The legacy's work lookups did not reach the top of this fixture,
        so these are the model's own tags - but they are pinned here, so
        that a change in them would be caught.
        """
        for order in ("a", "b"):
            tags = self._adapter(order).tags()
            track = tags[WorkId("rec-1")]
            metadata = track.as_metadata()
            for tag, value in self.MODEL.items():
                self.assertEqual(
                    metadata.get(tag), value,
                    "order %s %s" % (order, tag))
            # and the whole set of the level tags, so that a level that
            # was not there before would show
            self.assertEqual(
                {tag for tag in metadata
                 if tag.startswith('~cwp_workid')},
                {"~cwp_workid_0", "~cwp_workid_1", "~cwp_workid_2",
                 "~cwp_workid_3", "~cwp_workid_top"},
                "order %s" % order)

    @staticmethod
    def _adapter(order):
        directory = os.path.join(FIXTURES, "partial_order")
        works = [
            _load(os.path.join(directory, name))
            for name in sorted(os.listdir(directory))
            if name.startswith("work_")]
        recording = _load(os.path.join(directory, "rec_1.json"))
        relations = recording["relations"]
        if order == "a":
            # work-a's relation first
            relations = [relations[2], relations[0], relations[1]]
        return AlbumAdapter(
            works, [{"id": "rec-1", "relations": relations}],
            use_partial=True)

    def test_the_partly_performed_work_keeps_its_place(self):
        """The recording id stands where the partly performed work stood.

        The recording performs work-b partly and in full, and work-a in
        full. The recording id stands in for work-b in the first level,
        in the place the relations gave it - so the level holds the
        recording id and work-a, and the level above holds work-b.

        The legacy writes exactly this.
        """
        tags = self._adapter("b").tags()
        track = tags[WorkId("rec-1")]
        metadata = track.as_metadata()
        for tag, ids in self.LEGACY_FIRST_B.items():
            self.assertEqual(
                tuple(metadata.get(tag) or ()), ids,
                "%s" % (tag,))

    def test_the_order_of_the_relations_is_kept(self):
        """The level holds the ids in the order the relations gave them.

        With work-a named first, the recording id comes second - the
        partly performed work still stands where the relations put it.
        The legacy writes exactly this.
        """
        tags = self._adapter("a").tags()
        track = tags[WorkId("rec-1")]
        metadata = track.as_metadata()
        for tag, ids in self.LEGACY_FIRST_A.items():
            self.assertEqual(
                tuple(metadata.get(tag) or ()), ids,
                "%s" % (tag,))

    def test_a_partly_performed_work_after_a_fully_performed_one(self):
        """A partly performed work keeps its place after a full one.

        The recording performs work-a in full and work-b in part, and
        names work-a first. The recording id stands in for work-b, so
        the first level holds work-a and the recording id.

        A work performed only in part contributes nothing to the level
        above - the recording is not that work - so the level above
        holds the parent of the work that is performed in full. The
        legacy puts the partly performed work there instead; that is a
        difference the model makes on purpose, and it is what the
        legacy's own tags show too.
        """
        works = [
            {
                "id": "work-a",
                "title": "The first work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent"},
                    },
                ],
            },
            {
                "id": "work-b",
                "title": "The second work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent"},
                    },
                ],
            },
            {"id": "parent", "title": "The parent"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                    "attributes": ["partial"],
                },
            ],
        }]
        adapter = AlbumAdapter(works, recordings, use_partial=True)
        tags = adapter.tags()
        track = tags[w("rec-1")]
        # the recording id stands in for work-b, in work-b's place
        self.assertEqual(track.levels[0], [w("work-a"), w("rec-1")])
        # and the level above holds the parent of the work performed in
        # full
        self.assertEqual(track.levels[1], [w("parent")])

    def test_a_partly_performed_work_after_two_fully_performed_ones(self):
        """A partly performed work keeps its place after two full ones.

        The recording performs work-a and work-b in full and work-c in
        part and in full, naming them in that order. The first level
        holds work-a, work-b and the recording id - which stands in for
        work-c - so work-c goes last in the level above.

        The three works share a parent, so they are in one node and the
        level holds work-c alone.
        """
        works = [
            {
                "id": "work-a",
                "title": "The first work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent"},
                    },
                ],
            },
            {
                "id": "work-b",
                "title": "The second work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent"},
                    },
                ],
            },
            {
                "id": "work-c",
                "title": "The third work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent"},
                    },
                ],
            },
            {"id": "parent", "title": "The parent"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-c"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-c"},
                },
            ],
        }]
        adapter = AlbumAdapter(works, recordings, use_partial=True)
        tags = adapter.tags()
        track = tags[w("rec-1")]
        # the recording id stands in for work-c, in work-c's place
        self.assertEqual(
            track.levels[0], [w("work-a"), w("work-b"), w("rec-1")])
        # and the level above holds work-c, in the recording id's place
        self.assertEqual(track.levels[1], [w("work-c")])

    def test_a_partly_performed_work_between_works_with_a_shared_parent(self):
        """A partly performed work keeps its place among the others.

        The recording performs work-a and work-b in full - and they
        share a parent - then work-c in part and in full, then work-d in
        full. The first level holds work-a, work-b, the recording id -
        which stands in for work-c - and work-d.

        The level above holds work-c, in the recording id's place. The
        two works that share a parent contribute one id between them, so
        the place cannot be counted in the works that lead to the level.

        The legacy writes ~cwp_workid_0 = ('work-a', 'work-b', 'rec-1',
        'work-d') and ~cwp_workid_1 = ('work-c',).
        """
        def parent_works(name, parent):
            return {
                "id": name,
                "title": name,
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": parent},
                    },
                ],
            }

        works = [
            parent_works("work-a", "parent-p"),
            parent_works("work-b", "parent-p"),
            parent_works("work-c", "parent-r"),
            parent_works("work-d", "parent-q"),
            {"id": "parent-p", "title": "The parent P"},
            {"id": "parent-r", "title": "The parent R"},
            {"id": "parent-q", "title": "The parent Q"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-c"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-c"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-d"},
                },
            ],
        }]
        adapter = AlbumAdapter(works, recordings, use_partial=True)
        tags = adapter.tags()
        track = tags[w("rec-1")]
        # the recording id stands in for work-c, in work-c's place
        self.assertEqual(
            track.levels[0],
            [w("work-a"), w("work-b"), w("rec-1"), w("work-d")])
        # and the level above holds work-c, in the recording id's place
        self.assertEqual(track.levels[1], [w("work-c")])


class PartialFullFirstFixtureTestCase(unittest.TestCase):
    """A work related in full first and partly second.

    The expectations are the legacy's own: the fixture was run through
    the legacy and the tags it wrote are recorded here.
    """

    #: The legacy's tags for the fixture. It wrote
    #: workid_0 = ('work-b', 'work-a') - the work itself, because the
    #: first relation for it is a full one - and workid_top =
    #: ('work-b', 'work-a').
    LEGACY = {
        "~cwp_workid_0": ("work-b", "work-a"),
        "~cwp_workid_top": ("work-b", "work-a"),
    }

    @staticmethod
    def _adapter():
        directory = os.path.join(FIXTURES, "partial_full_first")
        works = [
            _load(os.path.join(directory, name))
            for name in sorted(os.listdir(directory))
            if name.startswith("work_")]
        recording = _load(os.path.join(directory, "rec_1.json"))
        return AlbumAdapter(works, [recording], use_partial=True)

    def test_the_work_keeps_its_own_id(self):
        """The first relation decides, so the work keeps its own id.

        The work is related in full first and partly second. The legacy
        reads the attributes of the first relation it finds for the
        work, so it is a work performed in full - the recording id does
        not stand in for it.

        The legacy writes workid_0 = ('work-b', 'work-a') and
        workid_top = ('work-b', 'work-a').
        """
        tags = self._adapter().tags()
        track = tags[WorkId("rec-1")]
        metadata = track.as_metadata()
        for tag, ids in self.LEGACY.items():
            self.assertEqual(
                tuple(metadata.get(tag) or ()), ids,
                "%s" % (tag,))

    def test_the_partly_performed_work_is_not_among_the_partial_ones(self):
        """The work is not among the partly performed works.

        The recording id stands in for a partly performed work, and this
        one is not partly performed - so it is not in that list, and the
        level above it does not hold it.
        """
        adapter = self._adapter()
        self.assertEqual(adapter.recordings[0].partial, [])
        tags = adapter.tags()
        track = tags[WorkId("rec-1")]
        self.assertEqual(track.levels[0], [w("work-b"), w("work-a")])


class PartialOnlyShapeTestCase(unittest.TestCase):
    """A partly performed work that is the recording's only work.

    The legacy stops at the partly performed work: its node holds the
    work and no parent, so the work's own parents are never reached.
    The model goes on - the work is performed in full as well, so it
    contributes itself and the chain continues above it.

    That is a difference the model makes on purpose, and this test
    says so.
    """
    @staticmethod
    def _adapter():
        works = [
            {
                "id": "work-a",
                "title": "A work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-a"},
                    },
                ],
            },
            {
                "id": "parent-a",
                "title": "The parent",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "top"},
                    },
                ],
            },
            {"id": "top", "title": "The top"},
        ]
        recordings = [{
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
        }]
        return AlbumAdapter(works, recordings, use_partial=True)

    def test_the_model_goes_on_where_the_legacy_stops(self):
        """The model goes on above the partly performed work.

        The legacy writes ~cwp_workid_0 = ('rec-1',) and
        ~cwp_workid_1 = ('work-a',) and stops there - the work's node
        has no parent. The model continues to the work's own parent,
        because the work is performed in full as well.
        """
        tags = self._adapter().tags()
        track = tags[WorkId("rec-1")]
        self.assertEqual(track.levels[0], [w("rec-1")])
        self.assertEqual(track.levels[1], [w("work-a")])
        self.assertEqual(track.levels[2], [w("parent-a")])


class EmptyNamesTestCase(unittest.TestCase):
    """The tags of a track whose works have no titles."""

    def test_the_top_work_tag_is_left_out_when_the_name_is_unknown(self):
        """The name tag is left out when there is no name.

        The legacy writes a tag only when it has a value, so an unknown
        name is no tag at all.
        """
        works = [
            {
                "id": "work-1",
                "title": "A work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "work-2"},
                    },
                ],
            },
            {"id": "work-2", "title": "Another work"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-1"},
                },
            ],
        }]
        adapter = AlbumAdapter(works, recordings)
        tags = adapter.tags()
        metadata = tags[WorkId("rec-1")].as_metadata()
        # the legacy writes the name tag only when it has a name
        self.assertNotIn('~cwp_work_top', metadata)
        self.assertEqual(metadata['~cwp_workid_top'], ("work-2",))


class LegacyProbeTestCase(unittest.TestCase):
    """The probe that runs the real legacy must reproduce a recording.

    The probe is how the adapter is checked against the legacy on a case
    that no fixture covers. But a probe that does not reproduce a recorded
    album proves nothing, so this checks it against the daphnis album -
    the one album with a partly performed work that has a parent.

    It runs the real legacy over the real fixture and compares the tags
    with the recorded ones.
    """

    def test_the_probe_reproduces_the_daphnis_album(self):
        """The probe gives the tags the legacy recorded for daphnis."""
        import json
        import os
        import sys

        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'tools')
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import legacy_probe

        fixdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'fixtures', 'daphnis')
        recorded = json.load(open(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'fixtures', 'legacy_tags.json')))['daphnis']['tracks']

        tracks = []
        for label in sorted(recorded):
            rec = json.load(open(os.path.join(fixdir, 'rec_%s.json' % label)))
            works = [r['work']['id'] for r in rec.get('relations') or ()
                     if r.get('type') == 'performance']
            tracks.append((label, 1, int(label.replace('d1t', '')),
                           rec['id'], '; '.join(works)))

        result = legacy_probe.run(fixdir, 'daph-rel', tracks, partial=True)
        for label in sorted(recorded):
            metadata = result[label].metadata
            for index in range(4):
                tag = '~cwp_workid_%d' % index
                want = recorded[label].get(tag)
                got = metadata.get(tag)
                if got is not None and isinstance(got, tuple):
                    got = list(got)
                self.assertEqual(
                    got, want,
                    "the probe's %s of %s differs from the recorded one"
                    % (tag, label))
            # the names and the counts too - they are what the vote and
            # the name fixes protect
            for tag in ('~cwp_work_top', '~cwp_work_group',
                        '~cwp_part_levels', '~cwp_work_part_levels'):
                want = recorded[label].get(tag)
                got = metadata.get(tag)
                if isinstance(got, tuple):
                    got = list(got)
                self.assertEqual(
                    got, want,
                    "the probe's %s of %s differs from the recorded one"
                    % (tag, label))


class LegacyNonDeterminismTestCase(unittest.TestCase):
    """The legacy's tags can depend on the order the lookups come back.

    A recording that performs two works whose parent trees do not meet
    makes the legacy fuse nodes as it walks, so the same album gives
    different tags from run to run. The adapter is deterministic there.

    This pins that difference, so that the note in AGENTS.md is a
    measured fact rather than a claim - and so that nobody "fixes" the
    adapter to match a legacy that cannot agree with itself.
    """

    def test_the_legacy_gives_different_tags_for_different_orders(self):
        """The legacy gives different tags for different lookup orders."""
        import os
        import sys

        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'tools')
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import legacy_probe

        directory = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'fixtures',
            'divergent')
        results = []
        for drain in ('fifo', 'lifo'):
            tracks = legacy_probe.tracks_of(directory)
            result = legacy_probe.run(directory, 'divergent-rel', tracks,
                                      partial=True, drain=drain)
            results.append([
                result[label].metadata.get('~cwp_workid_%d' % index)
                for label in sorted(result)
                for index in range(3)])
        self.assertNotEqual(
            results[0], results[1],
            "the legacy gave the same tags for both orders, so the "
            "non-determinism it is said to have is not there")

    def test_the_adapter_is_the_same_for_every_order(self):
        """The adapter gives the same tags whatever the order.

        The order the works are given in does not change the tags - the
        relations give the order, not the order they happen to arrive
        in. So the same album, read in any order, gives the same
        levels.
        """
        import itertools
        import math
        import os
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(root, 'plugins', 'classical_extras'))
        sys.path.insert(0, os.path.join(root, 'test', 'tools'))
        from hierarchy.adapter import AlbumAdapter
        import legacy_probe

        directory = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'fixtures',
            'divergent')
        works, recordings = legacy_probe.readings_of(directory)
        # the fixture has to be the one the test was written for
        self.assertEqual(len(works), 4)

        def levels_of(works_in):
            tags = AlbumAdapter(
                works_in, recordings, use_partial=True).tags()
            return [
                [work.mbid for work in level]
                for track in tags.values()
                for level in track.levels]

        expected = levels_of(works)
        # and the levels themselves
        self.assertEqual(
            expected,
            [['work-a', 'work-b'], ['parent-a', 'parent-b']])
        # the same works, given in every order there is - the relations
        # give the order, not the order the works happen to arrive in.
        # There are only a few of them, so all of them are tried.
        orders = list(itertools.permutations(works))
        self.assertEqual(len(orders), math.factorial(len(works)))
        for order in orders:
            self.assertEqual(
                levels_of(list(order)), expected,
                "the order the works were given in changed the levels")


class LegacyProbeFailureTestCase(unittest.TestCase):
    """The probe must undo its patch when the setup fails.

    The probe points the plugin's log directory at a temporary one, so
    that it does not write to the user's Picard directory. If the
    setup fails - or is interrupted - the patch has to be undone, or
    the rest of the process writes its logs into a directory that has
    been removed.
    """

    def test_the_patch_is_undone_when_the_setup_fails(self):
        """A failure in the setup leaves nothing behind."""
        import os
        import sys

        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'tools')
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import legacy_probe

        import picard.const as picard_const
        original = picard_const.USER_DIR

        # the temporary directory the probe makes is under the system's
        # temporary directory, wherever that is
        import tempfile
        temp_root = tempfile.gettempdir()
        before = set(os.listdir(temp_root))

        class Boom(BaseException):
            """Not an Exception, so the guard has to catch more."""

        def raising_setUp(self):
            raise Boom()

        saved = legacy_probe.T.TristanFullAlbumIntegrationTestCase.setUp
        legacy_probe.T.TristanFullAlbumIntegrationTestCase.setUp = (
            raising_setUp)
        try:
            directory = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'fixtures',
                'divergent')
            tracks = legacy_probe.tracks_of(directory)
            self.assertRaises(
                Boom, legacy_probe.run, directory, 'probe-rel', tracks)
        finally:
            legacy_probe.T.TristanFullAlbumIntegrationTestCase.setUp = saved

        # the directory is the user's again
        self.assertEqual(picard_const.USER_DIR, original)
        # and no temporary directory was left behind
        after = set(os.listdir(temp_root))
        left = [name for name in after - before
                if name.startswith('legacy-probe-')]
        self.assertEqual(left, [])


class NoChainTestCase(unittest.TestCase):
    """A recording that names no work."""

    def _adapter(self):
        return AlbumAdapter(
            [],
            [{"id": "rec-1", "relations": []}])

    def test_two_partial_works_give_the_recording_id_once(self):
        """Two works performed in part are both the recording.

        The node holds the works as a set, so it can hold the recording id
        only once - and the level's ids have to agree with it.
        """
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
                    "work": {"id": "work-b"},
                    "attributes": ["partial"],
                },
            ],
        }
        adapter = AlbumAdapter([], [recording])
        self.assertEqual(
            adapter.recordings[0].members,
            [w("rec-1")])
        metadata = adapter.tags()[w("rec-1")].as_metadata()
        self.assertEqual(metadata['~cwp_workid_0'], ("rec-1",))

    def test_a_work_related_full_then_partially_gives_one_id(self):
        """A work performed in full and then in part keeps its own id.

        The first relation decides: the legacy reads the attributes of
        the first relation it finds for the work, so a work related in
        full first is a work performed in full and the recording id
        does not stand in for it.

        The legacy writes ~cwp_workid_0 = ('rec-1', 'work-b') for such a
        recording - the recording id for the partly performed work-a,
        and work-b itself.
        """
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
                    "work": {"id": "work-b"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                    "attributes": ["partial"],
                },
            ],
        }
        adapter = AlbumAdapter([], [recording])
        members = adapter.recordings[0].members
        self.assertEqual(len(members), len(set(members)), members)
        self.assertEqual(members, [w("rec-1"), w("work-b")])

    def test_a_work_related_three_times_does_not_crash(self):
        """A third relation to the same work must not raise.

        The work is held once, whatever the number of relations that
        name it.
        """
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
                    "work": {"id": "work-b"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                    "attributes": ["partial"],
                },
            ],
        }
        adapter = AlbumAdapter([], [recording])
        self.assertEqual(
            adapter.recordings[0].members,
            [w("rec-1")])

    def test_every_relation_ordering_gives_one_id_per_work(self):
        """Every ordering of the relations gives the same level.

        Two works, each related up to three times and each either wholly
        or partly performed. A work performed in part is represented by
        the recording id, and the recording id is held once however many
        works stand in for it. A work related partly and wholly is
        represented by the recording id too - the recording is not the
        work.

        The rule itself is checked against the legacy's own tags by
        ``LegacyParityTestCase``; this checks that every ordering of the
        relations gives the same answer.
        """
        for relations in self._relation_orderings():
            recording = {"id": "rec-1", "relations": relations}
            adapter = AlbumAdapter([], [recording])
            members = adapter.recordings[0].members
            expected = self._expected_members(relations)
            self.assertEqual(
                members, expected,
                "%s -> %s" % (relations, members))

    def test_a_work_related_twice_is_the_recording_id(self):
        """The legacy's own tags for a work related partly and wholly.

        The Daphnis album has tracks whose recording is related to the
        same work twice - once partly and once wholly. The legacy writes
        the recording id for it, because the recording is not the work.

        The legacy's tags are compared with the model's, so this says
        what the legacy wrote and not what the relations suggest.
        """
        path = os.path.join(FIXTURES, "legacy_tags.json")
        with open(path, encoding="utf-8") as handle:
            legacy = json.load(handle)
        tags = _adapter("daphnis").tags()
        checked = 0
        for label, want in sorted(legacy["daphnis"]["tracks"].items()):
            recording = _load(os.path.join(
                FIXTURES, "daphnis", "rec_%s.json" % label))
            related = [
                relation for relation in (recording.get("relations") or ())
                if relation.get("type") == "performance"
                and (relation.get("work") or {}).get("id")]
            works = {(relation["work"]["id"]) for relation in related}
            if len(works) == len(related):
                # every work is related once - nothing to check
                continue
            checked += 1
            track = tags.get(WorkId(recording["id"]))
            self.assertIsNotNone(
                track, "daphnis %s has no tags" % label)
            level = track.levels[0]
            self.assertIn(
                w(recording["id"]), level,
                "daphnis %s: the recording id is not in the first level"
                % label)
            # the legacy's own first level, read as Picard stores it
            legacy_level = _as_picard_stores(
                want.get("~cwp_workid_0"))
            self.assertEqual(
                _as_picard_stores(
                    [work.mbid for work in level]),
                legacy_level,
                "daphnis %s: the first level" % label)
            # the work that is related twice is related partly first, so
            # the recording stands in for it - but a work related once in
            # full is in the level as itself
            for work in works:
                partial = _first_relation_is_partial(
                    related, work)
                if not partial:
                    continue
                self.assertNotIn(
                    w(work), level,
                    "daphnis %s: the work is in the first level" % label)
        self.assertTrue(
            checked, "no track had a work related twice")

    def test_the_performance_and_the_members_agree(self):
        """The model's performance and the first level's ids agree.

        They are read separately, so a test checks that they do not
        drift apart: the partly performed works are the same, and the
        works are the same.

        The complete set is checked through what it means for the first
        level: a work that is performed in full is in the level as
        itself, and a work that is only partly performed is not - the
        recording id stands in for it.
        """
        for relations in self._relation_orderings():
            recording = {"id": "rec-1", "relations": relations}
            adapter = AlbumAdapter([], [recording])
            entry = adapter.recordings[0]
            performance = entry.performance
            self.assertEqual(
                set(entry.partial), set(performance.partial),
                "%s" % (relations,))
            self.assertEqual(
                set(entry.performed), set(performance.works),
                "%s" % (relations,))
            # a work that is partly performed is the recording id in the
            # first level - unless it is performed in full as well, and
            # then it contributes itself and the recording id stands in
            # for it one level below
            for work in performance.partial:
                if work in performance.complete:
                    # the work contributes itself, so it is not in the
                    # first level - but the recording id is, in its
                    # place
                    self.assertNotIn(
                        work, entry.members,
                        "%s: the work is in the first level" % (relations,))
                    self.assertIn(
                        "rec-1",
                        [member.mbid for member in entry.members],
                        "%s: the recording id is not in the first level"
                        % (relations,))
                    continue
                self.assertNotIn(
                    work, entry.members,
                    "%s: the work is in the first level" % (relations,))
            for work in performance.works - performance.partial:
                self.assertIn(
                    work, entry.members,
                    "%s: the work is not in the first level" % (relations,))
            # and a work that is performed in full is one the model
            # knows about - the complete set says which works contribute
            # themselves to the level above
            for work in performance.complete:
                self.assertIn(
                    work, performance.works,
                    "%s: the work is not a work of the performance"
                    % (relations,))

    @staticmethod
    def _expected_members(relations):
        """The first level's ids for the given relations.

        Worked out from the relations themselves: the *first* relation
        for a work decides, because the legacy reads the attributes of
        the first relation it finds for the work. A work whose first
        relation is partly performed is the recording id, and a work
        whose first relation is whole is the work itself.
        """
        partial = set()
        order = []
        for relation in relations:
            work = relation['work']['id']
            if work not in order:
                order.append(work)
                if 'partial' in (relation.get('attributes') or ()):
                    partial.add(work)
        members = []
        for work in order:
            member = (
                "rec-1" if work in partial else work)
            if member not in members:
                members.append(member)
        return [w(member) for member in members]

    @staticmethod
    def _relation_orderings():
        """Every ordering of the relations over two works."""
        import itertools
        choices = [
            ("work-a", False), ("work-a", True),
            ("work-b", False), ("work-b", True),
        ]
        for count in (1, 2, 3):
            for combo in itertools.product(choices, repeat=count):
                yield [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": work},
                        "attributes": ["partial"] if partial else [],
                    }
                    for work, partial in combo
                ]

    def test_a_repeated_recording_is_held_once(self):
        """A release can list the same recording twice."""
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter([], [recording, dict(recording)])
        self.assertEqual(len(adapter.recordings), 1)
        self.assertEqual(len(adapter.tags()), 1)

    def test_the_first_of_two_recordings_is_kept(self):
        """The first of two entries for a recording is the one kept.

        The two are normally the same recording, but when they are not
        the first is kept - the tags are per recording, so there is
        nowhere for both to go.
        """
        first = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        second = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                },
            ],
        }
        adapter = AlbumAdapter([], [first, second])
        self.assertEqual(len(adapter.recordings), 1)
        self.assertEqual(
            adapter.recordings[0].members,
            [w("work-a")])
        # and the dropped one is recorded, so a caller can see it
        self.assertEqual(adapter.dropped, [w("rec-1")])
        # and the two disagreed, which is worth telling apart from a
        # recording that was simply listed twice
        self.assertEqual(adapter.conflicting, [w("rec-1")])

    def test_a_partly_performed_work_is_never_among_the_leaders(self):
        """A partly performed work is not among the works that lead to
        the level above the first.

        A work performed both in part and in full contributes itself, so
        it sits one level above the recording id - and the recording's
        own node holds the recording id, not the work. So the works that
        lead to the level above the first are the ones performed in
        full, and a partly performed work is not among them - so it
        never has to contribute its parents there.

        The recording performs a work in full as well, and that work's
        parent is the partly performed work - so the first level has a
        work that does lead to the level above, and the check below is
        about a list with something in it.
        """
        works = [
            {
                "id": "work-a",
                "title": "A work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
            {
                "id": "work-b",
                "title": "B work",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "parent-p"},
                    },
                ],
            },
            {
                "id": "parent-p",
                "title": "The parent P",
                "relations": [
                    {
                        "type": "parts",
                        "direction": "backward",
                        "work": {"id": "top"},
                    },
                ],
            },
            {"id": "top", "title": "The top"},
        ]
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                    "attributes": ["partial"],
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-b"},
                },
            ],
        }]
        adapter = AlbumAdapter(works, recordings, use_partial=True)
        leaders = []
        original = adapter._works_leading_to

        def spy(below_node, above_node, below):
            found = original(below_node, above_node, below)
            leaders.append(found)
            return found

        adapter._works_leading_to = spy
        adapter.tags()
        partial = set(adapter.recordings[0].partial)
        # the leaders were looked for - otherwise the check below would
        # be about nothing
        self.assertTrue(leaders, "the leaders were never looked for")
        # and the first level has a work that leads to the level above,
        # so the check is about something
        self.assertTrue(
            leaders[0], "the first level has no leader")
        # the first level's leaders are the ones that lead to the level
        # above the first - the others are higher up, where a partly
        # performed work can be among them
        for work in leaders[0]:
            self.assertNotIn(
                work, partial,
                "a partly performed work is among the leaders")

    def test_a_recording_on_two_tracks_votes_twice(self):
        """A recording on two tracks votes twice for its name.

        The legacy votes per *track*, so a recording that is on two
        tracks votes twice for the names of its candidate tops. The
        multiplicity says how many tracks each recording is on.

        The winner is checked through the tags, so that the vote the
        tags use is the one that is checked.
        """
        works = [
            {"id": "work-a", "title": "The first work"},
            {"id": "work-b", "title": "The second work"},
        ]
        # three recordings: one for each work, and one for both - so
        # that both names are candidates for the third one
        recordings = [
            {
                "id": "rec-1",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-a"},
                    },
                ],
            },
            {
                "id": "rec-2",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
            {
                "id": "rec-3",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-a"},
                    },
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
        ]
        names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        # Each name has two votes - rec-1 and rec-3 for the first one,
        # rec-2 and rec-3 for the second one. So the two are tied, and
        # the recording that has both names as candidates keeps both.
        adapter = AlbumAdapter(
            works, recordings, names=names, use_partial=True)
        tags = adapter.tags()
        self.assertEqual(
            tags[WorkId("rec-1")].top_names, ["The first work"])
        self.assertEqual(
            tags[WorkId("rec-2")].top_names, ["The second work"])
        self.assertEqual(
            tags[WorkId("rec-3")].top_names,
            ["The first work", "The second work"])
        # rec-1 is on two tracks, so it votes twice - and the first name
        # wins outright, so the recording with both names now names one
        adapter = AlbumAdapter(
            works, recordings, names=names, use_partial=True,
            multiplicity={w("rec-1"): 2})
        tags = adapter.tags()
        self.assertEqual(
            tags[WorkId("rec-1")].top_names, ["The first work"])
        # rec-2's only candidate is the second work, so it keeps it -
        # the vote picks among a track's own candidates
        self.assertEqual(
            tags[WorkId("rec-2")].top_names, ["The second work"])
        self.assertEqual(
            tags[WorkId("rec-3")].top_names, ["The first work"])

    def test_a_multiplicity_given_as_a_string_is_used(self):
        """A multiplicity given as a plain string is used.

        A work id is not a string - it is a dataclass - so a caller that
        has the ids as strings would otherwise be silently ignored.
        """
        works = [
            {"id": "work-a", "title": "The first work"},
            {"id": "work-b", "title": "The second work"},
        ]
        recordings = [
            {
                "id": "rec-1",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-a"},
                    },
                ],
            },
            {
                "id": "rec-2",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
        ]
        names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        adapter = AlbumAdapter(
            works, recordings, names=names, use_partial=True,
            multiplicity={"rec-1": 2})
        self.assertEqual(adapter.multiplicity, {w("rec-1"): 2})
        candidates = {
            w("rec-1"): frozenset({w("work-a")}),
            w("rec-2"): frozenset({w("work-b")}),
        }
        self.assertEqual(
            adapter._votes(candidates),
            {"The first work": 2, "The second work": 1})

    def test_a_malformed_relation_is_skipped(self):
        """A relation that is not an object is skipped.

        The response is guarded against being malformed, but the
        relations inside it were not - so a relation that is a string or
        a null would have raised out of the tagging and taken the whole
        release with it.
        """
        good = {
            "id": "work-a",
            "title": "A work",
            "relations": [
                {
                    "type": "parts",
                    "direction": "backward",
                    "work": {"id": "work-b"},
                },
            ],
        }
        adapter = AlbumAdapter(
            [good, {"id": "work-c", "relations": [
                "bogus", None, 42,
                {"type": "parts", "work": "not an object"}]}],
            [{"id": "rec-1", "relations": [
                "bogus",
                None,
                {"type": "performance", "work": "not an object"},
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ]}],
            use_partial=True)
        tags = adapter.tags()
        self.assertIn(WorkId("rec-1"), tags)

    def test_attributes_that_are_not_a_list_are_no_attributes(self):
        """Attributes that are not a list are no attributes.

        A string would be searched as a list of characters, and
        ``'partial' in 'partially'`` is true - so a relation whose
        attributes are a string would be taken as a partly performed
        one.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                    "attributes": "partially",
                },
            ],
        }
        performance = read_performance(recording, use_partial=True)
        self.assertEqual(performance.partial, frozenset())
        self.assertEqual(performance.complete, frozenset({w("work-a")}))
        # a list of names is read as it is
        recording["relations"][0]["attributes"] = ["partial"]
        performance = read_performance(recording, use_partial=True)
        self.assertEqual(performance.partial, frozenset({w("work-a")}))

    def test_a_multiplicity_below_one_is_refused(self):
        """A count that is not a whole number of tracks is refused.

        A recording is on at least one track. A count of zero would take
        votes away and could change the winner of the album's vote - so
        it is refused rather than used. So is a count that is not a
        whole number: a recording is not on one and a half tracks.
        """
        for count in (0, -1, 1.5, "2", True):
            self.assertRaises(
                ValueError, AlbumAdapter, [], [],
                multiplicity={w("rec-1"): count})

    def test_a_multiplicity_for_a_recording_that_is_not_there_is_reported(self):
        """A count for a recording that is not in the release is reported.

        A count for a recording that is not there has no effect - and a
        misspelled id would have the same effect as a count that was not
        given at all. The caller is told.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter(
            [], [recording], multiplicity={w("rec-2"): 2})
        self.assertEqual(adapter.unused_multiplicity, [w("rec-2")])
        # a count for a recording that is there is used
        adapter = AlbumAdapter(
            [], [recording], multiplicity={w("rec-1"): 2})
        self.assertEqual(adapter.unused_multiplicity, [])

    def test_the_top_names_are_the_same_for_every_track_that_shares_a_top(self):
        """The tracks that share a top name it the same way.

        The vote is what makes them agree. With no vote at all - no
        candidate top has a title - the legacy takes the first name, and
        the names come in the order the relations gave them.

        So the two orders of the same two works name the top
        differently - the legacy's own function was run on this case,
        and it takes the first name of the list it is given.
        """
        names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        adapter = AlbumAdapter([], [], names=names, use_partial=True)
        votes = {}
        self.assertEqual(
            adapter._top_names([w("work-b"), w("work-a")], votes),
            ["The second work"])
        self.assertEqual(
            adapter._top_names([w("work-a"), w("work-b")], votes),
            ["The first work"])

    def test_a_recording_listed_twice_votes_twice(self):
        """A recording listed twice is a recording on two tracks.

        The legacy votes once per *track*, and a release that lists the
        same recording twice is that recording on two tracks. So it
        votes twice - the adapter has the count in hand and does not
        throw it away.

        The recording that has both names as its candidates is the one
        that shows it: with the two names tied it keeps both, and with
        the extra vote the first name wins outright.
        """
        works = [
            {"id": "work-a", "title": "The first work"},
            {"id": "work-b", "title": "The second work"},
        ]
        recordings = [
            {
                "id": "rec-1",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-a"},
                    },
                ],
            },
            {
                "id": "rec-2",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
            {
                "id": "rec-3",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-a"},
                    },
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-b"},
                    },
                ],
            },
        ]
        names = WorkNames(titles={
            w("work-a"): "The first work",
            w("work-b"): "The second work",
        })
        # each name has two votes - rec-1 and rec-3 for the first one,
        # rec-2 and rec-3 for the second one - so the two are tied
        adapter = AlbumAdapter(
            works, recordings, names=names, use_partial=True)
        tags = adapter.tags()
        self.assertEqual(
            tags[WorkId("rec-3")].top_names,
            ["The first work", "The second work"])
        # rec-1 is listed twice, so it votes twice - and the first name
        # wins outright
        adapter = AlbumAdapter(
            works, recordings + [dict(recordings[0])], names=names,
            use_partial=True)
        self.assertEqual(adapter.dropped, [w("rec-1")])
        self.assertEqual(
            adapter.tracks_per_recording,
            {w("rec-1"): 2, w("rec-2"): 1, w("rec-3"): 1})
        tags = adapter.tags()
        self.assertEqual(
            tags[WorkId("rec-3")].top_names, ["The first work"])

    def test_a_recording_that_is_not_an_object_raises(self):
        """A recording that is not an object raises.

        The reader is called directly here, so it says what it cannot
        use - rather than raising out of the tagging further down.
        """
        for recording in ("a string", [], 42, None):
            self.assertRaises(ValueError, read_performance, recording)
        # a response with no id raises as well
        self.assertRaises(ValueError, read_performance, {})
        # and so does one whose id is not a string
        self.assertRaises(ValueError, read_performance, {"id": 42})

    def test_a_multiplicity_for_a_recording_that_names_no_work_is_reported(self):
        """A count for a recording that names no work is reported.

        Such a recording has no chain and no candidates, so the count
        has no effect - the same as a count for a recording that is not
        in the release at all.
        """
        adapter = AlbumAdapter(
            [], [{"id": "rec-1", "relations": []}],
            multiplicity={w("rec-1"): 2})
        self.assertEqual(adapter.unused_multiplicity, [w("rec-1")])

    def test_the_multiplicities_that_had_no_effect_are_worked_out(self):
        """The counts that had no effect are worked out when they are read.

        A caller that reads the list without building the hierarchy first
        gets the right answer - a misspelled id would not go unreported.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter(
            [{"id": "work-a", "title": "A work"}], [recording],
            multiplicity={w("rec-2"): 2})
        self.assertEqual(adapter.unused_multiplicity, [w("rec-2")])

    def test_a_multiplicity_for_a_recording_that_names_a_work_is_used(self):
        """A count for a recording that names a work is used.

        The recording's own relation puts the work in the graph, so the
        work responses of the release are not needed for it - and the
        count has its effect.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter(
            [], [recording], multiplicity={w("rec-1"): 2})
        self.assertEqual(adapter.unused_multiplicity, [])

    def test_a_track_with_no_top_gets_no_tags(self):
        """A track with levels but no top gets no tags at all.

        Half a track's tags would name a level the top does not reach.
        """
        tags = TrackTags(
            key=WorkId("rec-1"),
            levels=[[w("work-a")]],
            top=[],
            top_names=["A work"],
            group="A group",
            part_levels=0,
            work_part_levels=0)
        self.assertEqual(tags.as_metadata(), {})

    def test_a_relation_whose_work_id_is_not_a_string_is_skipped(self):
        """A relation whose work id is not a string is skipped.

        The ids are strings. One that is not - a list, say - cannot be
        put in a set, so it would have raised out of the tagging and
        taken the whole release with it.
        """
        adapter = AlbumAdapter(
            [{"id": "work-a", "title": "A work"}],
            [{"id": "rec-1", "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": ["a list"]},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": 42},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ]}],
            use_partial=True)
        tags = adapter.tags()
        self.assertIn(WorkId("rec-1"), tags)

    def test_the_group_is_the_same_vote_as_the_top(self):
        """The group is the same vote as the top work's name.

        The legacy works the group out from the vote it uses for the
        top work's name - the same vote and the same names - so the
        two tags of a track cannot disagree. Here the top of rec-3 is
        one name and the group was another, because the group was
        worked out from a vote of its own.

        The legacy was run on this case: it writes the top and the
        group as the same name.
        """
        works = [
            {"id": "work-zed", "title": "Zed"},
            {"id": "work-ann", "title": "Ann"},
        ]

        def recording(rid, wid):
            return {
                "id": rid,
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": wid},
                    },
                ],
            }

        both = {
            "id": "rec-3",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-zed"},
                },
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-ann"},
                },
            ],
        }
        names = WorkNames(titles={
            w("work-zed"): "Zed",
            w("work-ann"): "Ann",
        })
        # rec-1 is on two tracks, so it votes twice and "Zed" wins
        adapter = AlbumAdapter(
            works,
            [recording("rec-1", "work-zed"),
             recording("rec-2", "work-ann"),
             both,
             recording("rec-1", "work-zed")],
            names=names, use_partial=True)
        tags = adapter.tags()
        self.assertEqual(tags[WorkId("rec-3")].top_names, ["Zed"])
        # the group is the same vote, so it is the same name
        self.assertEqual(tags[WorkId("rec-3")].group, "Zed")
        self.assertEqual(tags[WorkId("rec-1")].group, "Zed")
        self.assertEqual(tags[WorkId("rec-2")].group, "Ann")

        # and a tie keeps every name for the top, in the order the
        # relations gave them, but the group is one of them - the first
        # of them, so that the group is never split. The legacy was run
        # on this case: it keeps both names for the top and writes the
        # group as the first of them.
        adapter = AlbumAdapter(
            works,
            [recording("rec-1", "work-zed"),
             recording("rec-2", "work-ann"),
             both],
            names=names, use_partial=True)
        tags = adapter.tags()
        self.assertEqual(
            tags[WorkId("rec-3")].top_names, ["Zed", "Ann"])
        # the group is the smallest of the tied names by normalised name
        self.assertEqual(tags[WorkId("rec-3")].group, "Ann")

    def test_the_group_is_one_of_the_top_names(self):
        """The group is one of the names the top work's tag holds.

        Two names that differ only in punctuation normalise to the same
        thing, so both can win the vote - and the group has to be one of
        the names that won, not some other spelling of it.

        The legacy's own two functions were run on this case: the top
        keeps both names and the group is the first of them.
        """
        works = [
            {"id": "work-a", "title": "alpha"},
            {"id": "work-b", "title": "Alpha."},
            {
                "id": "child",
                "title": "The child",
                "relations": [
                    {
                        "type": "parts",
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
        recordings = [{
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "child"},
                },
            ],
        }]
        names = WorkNames(titles={
            w("work-a"): "alpha",
            w("work-b"): "Alpha.",
            w("child"): "The child",
        })
        tags = AlbumAdapter(
            works, recordings, names=names, use_partial=True).tags()
        track = tags[WorkId("rec-1")]
        # the two names are tied, so both are kept for the top
        self.assertEqual(track.top_names, ["alpha", "Alpha."])
        # and the group is one of them - the first, since the two
        # normalise to the same thing
        self.assertIn(track.group, track.top_names)
        self.assertEqual(track.group, "alpha")

    def test_the_group_is_always_one_of_the_top_names(self):
        """The group is always one of the names the top work's tag holds.

        The group is the shuffle key and the top is the name, and the
        two are worked out from the same vote - so the group must be
        one of the names that won. If it were not, the two tags of a
        track would disagree about which name the album chose.
        """
        for album in sorted(USE_PARTIAL):
            adapter = _adapter(album)
            for track in adapter.tags().values():
                if not track.top_names:
                    continue
                self.assertIn(
                    track.group, track.top_names,
                    "%s: the group is not one of the top's names" % album)

    def test_a_response_that_is_not_an_object_is_skipped(self):
        """A response that is not an object is skipped.

        A response is JSON, and it is expected to be an object - but a
        malformed one would raise out of the tagging and take the whole
        release with it.
        """
        good = {
            "id": "work-a",
            "title": "A work",
            "relations": [
                {
                    "type": "parts",
                    "direction": "backward",
                    "work": {"id": "work-b"},
                },
            ],
        }
        adapter = AlbumAdapter(
            ["a string", [], 42, good],
            ["a string", [], {"id": "rec-1", "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ]}],
            use_partial=True)
        tags = adapter.tags()
        self.assertIn(WorkId("rec-1"), tags)

    def test_a_work_without_an_id_is_skipped(self):
        """A work response with no id is skipped, and the rest is used.

        A failed lookup gives a response with no id. It cannot be placed
        in the graph - but it must not stop the rest of the release from
        being tagged.
        """
        good = {
            "id": "work-a",
            "title": "A work",
            "relations": [
                {
                    "type": "parts",
                    "direction": "backward",
                    "work": {"id": "work-b"},
                },
            ],
        }
        adapter = AlbumAdapter(
            [{}, good, None],
            [{
                "id": "rec-1",
                "relations": [
                    {
                        "type": "performance",
                        "direction": "forward",
                        "work": {"id": "work-a"},
                    },
                ],
            }],
            use_partial=True)
        tags = adapter.tags()
        self.assertIn(WorkId("rec-1"), tags)

    def test_a_recording_without_an_id_is_skipped(self):
        """A response with no id is skipped, and the rest is tagged.

        A track with no recording attached, or a failed lookup, gives a
        response with no id. It cannot be tagged - but it must not stop
        the rest of the release from being tagged.
        """
        good = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter([], [{}, good, None])
        self.assertEqual(len(adapter.recordings), 1)
        self.assertEqual(adapter.recordings[0].members, [w("work-a")])
        tags = adapter.tags()
        self.assertIn(WorkId("rec-1"), tags)

    def test_a_repeated_recording_with_another_relation_is_a_conflict(self):
        """A repeated recording whose second relation differs is a
        conflict.

        The two entries perform the same work, but the second relation
        to it differs - one in full, one partly. The work is in the
        complete set in one and not in the other, so the two give
        different hierarchies and different tags.
        """
        def relation(attributes):
            return {
                "type": "performance",
                "direction": "forward",
                "work": {"id": "work-a"},
                "attributes": attributes,
            }

        first = {
            "id": "rec-1",
            "relations": [relation(["partial"]), relation([])],
        }
        second = {
            "id": "rec-1",
            "relations": [relation(["partial"]), relation(["partial"])],
        }
        adapter = AlbumAdapter([], [first, second])
        self.assertEqual(adapter.dropped, [w("rec-1")])
        self.assertEqual(adapter.conflicting, [w("rec-1")])

    def test_a_repeated_recording_is_not_a_conflict(self):
        """The same recording listed twice is not a conflict.

        A release can list the same recording on two tracks - the same
        performance of the same works. The second entry is dropped, but
        the two did not disagree, so there is nothing to tell the caller
        about.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter([], [recording, dict(recording)])
        self.assertEqual(adapter.dropped, [w("rec-1")])
        self.assertEqual(adapter.conflicting, [])

    def test_a_repeated_recording_in_another_order_is_a_conflict(self):
        """The same recording listed twice in another order is a conflict.

        The two entries perform the same works, but in a different order
        - and the order decides the tag of the level above the first, so
        the two would write different tags. The first entry's wins, and
        the caller is told that the two disagreed.
        """
        def relation(work):
            return {
                "type": "performance",
                "direction": "forward",
                "work": {"id": work},
                "attributes": ["partial"],
            }

        first = {
            "id": "rec-1",
            "relations": [relation("work-a"), relation("work-b")],
        }
        second = {
            "id": "rec-1",
            "relations": [relation("work-b"), relation("work-a")],
        }
        adapter = AlbumAdapter([], [first, second])
        self.assertEqual(adapter.dropped, [w("rec-1")])
        self.assertEqual(adapter.conflicting, [w("rec-1")])

    def test_a_shared_recording_gives_both_tracks_the_same_tags(self):
        """A release can list the same recording on two tracks.

        The tags are the recording's, so both tracks get them - the
        caller looks them up by the recording id.
        """
        recording = {
            "id": "rec-1",
            "relations": [
                {
                    "type": "performance",
                    "direction": "forward",
                    "work": {"id": "work-a"},
                },
            ],
        }
        adapter = AlbumAdapter([], [recording, dict(recording)])
        tags = adapter.tags()
        self.assertEqual(len(tags), 1)
        self.assertIn(w("rec-1"), tags)
        self.assertEqual(
            tags[w("rec-1")].levels[0], [w("work-a")])

    def test_the_recording_is_in_the_result(self):
        """A recording with no chain is still there, with no levels."""
        tags = self._adapter().tags()
        self.assertIn(w("rec-1"), tags)
        self.assertEqual(tags[w("rec-1")].levels, [])

    def test_it_gets_no_tags(self):
        """A track with no hierarchy gets no tags at all."""
        tags = self._adapter().tags()
        self.assertEqual(tags[w("rec-1")].as_metadata(), {})


class LegacyParityTestCase(unittest.TestCase):
    """The adapter against the legacy's own tags.

    The fixture holds the tags the legacy plugin writes for the nine
    recorded releases. The adapter has to write the same ones, except
    where the model deliberately differs - and those differences are
    listed in AGENTS.md.
    """

    @classmethod
    def setUpClass(cls):
        path = os.path.join(FIXTURES, 'legacy_tags.json')
        with open(path, encoding='utf-8') as handle:
            cls.legacy = json.load(handle)
        path = os.path.join(FIXTURES, 'legacy_tags_full.json')
        with open(path, encoding='utf-8') as handle:
            cls.full = json.load(handle)

    #: The tracks whose tags differ from the legacy's in more than extra
    #: ids, and why. The Gayne tracks sit one level deeper, because the
    #: model keeps the scene level; the Swan Lake tracks end at the
    #: original edition rather than the Drigo one, and their top holds
    #: both editions where the legacy's held one; and the Nutcracker,
    #: Planets and Requiem tracks have a top that holds more works than
    #: the legacy's.
    DIFFERING = {
        "daphnis": [
            "d1t11", "d1t6", "d1t8",
        ],
        "gayne": [
            "d1t10",
            "d1t11",
            "d1t6",
            "d1t7",
            "d1t8",
            "d1t9",
        ],
        "nutcracker": [
            "d1t1",
        ],
        "planets": [
            "t7",
        ],
        "requiem": [
            "t1",
        ],
        "swanlake_full": [
            "d1t1", "d1t10", "d1t11", "d1t12", "d1t13", "d1t14",
            "d1t15", "d1t16", "d1t17", "d1t19", "d1t2", "d1t20",
            "d1t21", "d1t22", "d1t23", "d1t3", "d1t4", "d1t5",
            "d1t6", "d1t7", "d1t8", "d1t9", "d2t1", "d2t13",
            "d2t14", "d2t15", "d2t17", "d2t2", "d2t3", "d2t4",
            "d2t5", "d2t6", "d2t7", "d2t8", "d2t9",
        ],
        "tristan": [
            "t2",
        ],
    }

    #: The tracks whose a level holds the legacy's ids and more, and
    #: nothing else differs. The model keeps both Swan Lake editions
    #: where the legacy picked one, and the extra ids come after the
    #: legacy's.
    HOLDING_MORE = {
        "swanlake_jarvi": [
            "t1", "t10", "t13", "t15", "t16", "t17", "t18", "t2",
            "t3", "t4", "t6", "t7", "t9",
        ],
    }

    #: The tracks whose level counts differ from the legacy's. The Gayne
    #: tracks sit one level deeper, because the model keeps the scene
    #: level; the Swan Lake tracks that end at the original edition sit
    #: one deeper too; and the two tracks whose top holds more works
    #: have a deeper tree under it.
    DEEPER = {
        "gayne": [
            "d1t10",
            "d1t11",
            "d1t6",
            "d1t7",
            "d1t8",
            "d1t9",
        ],
        "nutcracker": ["d1t1"],
        "requiem": ["t1"],
        "swanlake_full": [
            "d1t17", "d1t19", "d1t20", "d1t21", "d1t22",
        ],
    }

    #: The tags that differ from the legacy's, for the tracks that
    #: differ. The exemption is per *track*, so without this a track
    #: that is exempt because of one tag could hide a difference in
    #: another - and the check would still pass.
    #:
    #: The differences are the model's own decisions: a level that ends
    #: at the other edition of Swan Lake, a scene level the model keeps
    #: and the legacy did not, and the counts that follow from them.
    DIFFERING_TAGS = {
        "daphnis": {
            "d1t11": ["~cwp_part_0", "~cwp_work_0"],
            "d1t6": ["~cwp_part_0", "~cwp_work_0"],
            "d1t8": ["~cwp_part_0", "~cwp_work_0"],
        },
        "gayne": {
            "d1t10": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_part_levels", "~cwp_work_1", "~cwp_work_2",
                "~cwp_work_3", "~cwp_workid_1", "~cwp_workid_2",
                "~cwp_workid_3",
            ],
            "d1t11": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_part_levels", "~cwp_work_1", "~cwp_work_2",
                "~cwp_work_3", "~cwp_workid_1", "~cwp_workid_2",
                "~cwp_workid_3",
            ],
            "d1t6": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_part_levels", "~cwp_work_1", "~cwp_work_2",
                "~cwp_work_3", "~cwp_workid_1", "~cwp_workid_2",
                "~cwp_workid_3",
            ],
            "d1t7": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_part_levels", "~cwp_work_1", "~cwp_work_2",
                "~cwp_work_3", "~cwp_workid_1", "~cwp_workid_2",
                "~cwp_workid_3",
            ],
            "d1t8": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_part_levels", "~cwp_work_1", "~cwp_work_2",
                "~cwp_work_3", "~cwp_workid_1", "~cwp_workid_2",
                "~cwp_workid_3",
            ],
            "d1t9": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_part_levels", "~cwp_work_1", "~cwp_work_2",
                "~cwp_work_3", "~cwp_workid_1", "~cwp_workid_2",
                "~cwp_workid_3",
            ],
        },
        "nutcracker": {
            "d1t1": [
                "~cwp_part_0", "~cwp_work_0", "~cwp_work_1",
                "~cwp_work_part_levels", "~cwp_workid_1",
                "~cwp_workid_top",
            ],
        },
        "planets": {
            "t7": ["~cwp_part_0", "~cwp_work_0", "~cwp_work_1",
                   "~cwp_workid_1",
                    "~cwp_workid_top"],
        },
        "requiem": {
            "t1": [
                "~cwp_part_0",
                "~cwp_work_0",
                "~cwp_work_part_levels", "~cwp_workid_1",
                "~cwp_workid_top",
            ],
        },
        "swanlake_full": {
            "d1t1": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t10": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t11": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t12": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t13": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t14": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t15": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t16": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t17": [
                "~cwp_part_1", "~cwp_part_2", "~cwp_part_levels",
                "~cwp_work_1", "~cwp_work_2", "~cwp_work_3",
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_3",
                "~cwp_workid_top",
            ],
            "d1t19": [
                "~cwp_part_1", "~cwp_part_2", "~cwp_part_levels",
                "~cwp_work_1", "~cwp_work_2", "~cwp_work_3",
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_3",
                "~cwp_workid_top",
            ],
            "d1t2": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t20": [
                "~cwp_part_1", "~cwp_part_2", "~cwp_part_levels",
                "~cwp_work_1", "~cwp_work_2", "~cwp_work_3",
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_3",
                "~cwp_workid_top",
            ],
            "d1t21": [
                "~cwp_part_1", "~cwp_part_2", "~cwp_part_levels",
                "~cwp_work_1", "~cwp_work_2", "~cwp_work_3",
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_3",
                "~cwp_workid_top",
            ],
            "d1t22": [
                "~cwp_part_1", "~cwp_part_2", "~cwp_part_levels",
                "~cwp_work_1", "~cwp_work_2", "~cwp_work_3",
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_3",
                "~cwp_workid_top",
            ],
            "d1t23": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t3": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t4": ["~cwp_workid_2", "~cwp_workid_top"],
            "d1t5": [
                "~cwp_part_1", "~cwp_work_1", "~cwp_workid_2",
                "~cwp_workid_3", "~cwp_workid_top",
            ],
            "d1t6": [
                "~cwp_part_1", "~cwp_work_1", "~cwp_workid_2",
                "~cwp_workid_3", "~cwp_workid_top",
            ],
            "d1t7": [
                "~cwp_part_1", "~cwp_work_1", "~cwp_workid_2",
                "~cwp_workid_3", "~cwp_workid_top",
            ],
            "d1t8": [
                "~cwp_part_1", "~cwp_work_1", "~cwp_workid_2",
                "~cwp_workid_3", "~cwp_workid_top",
            ],
            "d1t9": [
                "~cwp_part_1", "~cwp_work_1", "~cwp_workid_2",
                "~cwp_workid_3", "~cwp_workid_top",
            ],
            "d2t1": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t13": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_work_1", "~cwp_work_2", "~cwp_workid_1",
                "~cwp_workid_2", "~cwp_workid_3", "~cwp_workid_top",
            ],
            "d2t14": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t15": [
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_top",
            ],
            "d2t17": [
                "~cwp_workid_1", "~cwp_workid_2", "~cwp_workid_top",
            ],
            "d2t2": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t3": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t4": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t5": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t6": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t7": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t8": ["~cwp_workid_2", "~cwp_workid_top"],
            "d2t9": [
                "~cwp_part_0", "~cwp_part_1", "~cwp_part_2",
                "~cwp_work_1", "~cwp_work_2", "~cwp_workid_1",
                "~cwp_workid_2", "~cwp_workid_3", "~cwp_workid_top",
            ],
        },
        "tristan": {
            "t2": ["~cwp_part_1", "~cwp_work_1"],
        },
    }

    @staticmethod
    def _ids(value):
        """The ids a tag holds, in order.

        The tag is read as Picard stores it: a tuple of one id is a bare
        string, and a tuple of more is a list. The legacy's own tags were
        captured from Picard's metadata, so they are in that form too.
        A tuple written as text is read as well, so that a tag written
        by hand is understood.
        """
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value)
        text = str(value).strip()
        if text.startswith('(') and text.endswith(')'):
            import ast
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, (list, tuple)):
                return tuple(str(item).strip() for item in parsed)
        if ';' in text:
            return tuple(
                part.strip() for part in text.split(';') if part.strip())
        return (text,)

    def test_the_fixture_holds_every_release(self):
        self.assertEqual(
            sorted(self.legacy),
            sorted(USE_PARTIAL))

    def test_the_listed_tracks_exist(self):
        """Every track named in the lists is in the fixture.

        A name that is not there would be silently ignored - the test
        that uses it would pass without checking anything.
        """
        for album, labels in self.DIFFERING.items():
            for label in labels:
                self.assertIn(
                    label, self.legacy[album]['tracks'],
                    "%s %s is not in the fixture" % (album, label))
        for album, labels in self.HOLDING_MORE.items():
            for label in labels:
                self.assertIn(
                    label, self.legacy[album]['tracks'],
                    "%s %s is not in the fixture" % (album, label))
        for album, labels in self.DEEPER.items():
            for label in labels:
                self.assertIn(
                    label, self.legacy[album]['tracks'],
                    "%s %s is not in the fixture" % (album, label))

    def test_the_tags_match_the_legacy(self):
        """Every tag of every track, against the legacy's own values.

        The ids are compared in order, because the order is the adapter's
        whole purpose. A level's ids may hold *more* than the legacy's -
        that is the model's decision to keep both Swan Lake editions -
        and the extra ids come after the legacy's. Only the tracks named
        in HOLDING_MORE may hold more.

        The tags compared are the ones the legacy wrote for that track,
        plus the ones the adapter wrote - so a level the adapter has and
        the legacy did not would be caught.

        Every tag of a track is compared, not just the first one that
        differs - so a track that is exempt because of one tag cannot
        hide a difference in another. The tags that differ are recorded
        as well as the tracks, so that a difference in a tag the
        exemption does not name would show.
        """
        differing = {}
        by_tag = {}
        for album, expected in sorted(self.full.items()):
            tags = _adapter(album).tags()
            holding_more = self.HOLDING_MORE.get(album) or []
            for label, want in sorted(expected['tracks'].items()):
                track = tags.get(WorkId(_recording_id(album, label)))
                if track is None:
                    differing.setdefault(album, set()).add(label)
                    continue
                metadata = track.as_metadata()
                for tag in self._tags_of(metadata, want):
                    if tag.startswith('~cwp_workid'):
                        got = self._ids(metadata.get(tag))
                        wanted = self._ids(want.get(tag))
                    else:
                        # The name and the group are text, not ids - but
                        # they are read as Picard stores them, so that a
                        # tuple of names and a list of names are the
                        # same. A tied top is written as a tuple.
                        got = _as_picard_stores(metadata.get(tag))
                        wanted = _as_picard_stores(want.get(tag))
                    if got == wanted:
                        continue
                    if (tag.startswith('~cwp_workid')
                            and label in holding_more
                            and self._holds_more(got, wanted)):
                        continue
                    differing.setdefault(album, set()).add(label)
                    by_tag.setdefault(album, {}).setdefault(
                        label, set()).add(tag)
        self.assertEqual(
            {album: sorted(labels)
             for album, labels in differing.items()},
            self.DIFFERING,
            "the tracks that differ from the legacy are not the "
            "documented ones")
        self.assertEqual(
            {album: {label: sorted(tags)
                      for label, tags in labels.items()}
             for album, labels in by_tag.items()},
            self.DIFFERING_TAGS,
            "the tags that differ from the legacy are not the "
            "documented ones")

    #: The tag families the adapter writes, and so the ones the
    #: comparison covers. The full oracle holds every tag the legacy
    #: writes - the part names, the genre and date tags - and the
    #: adapter does not write those yet: they are the remaining
    #: migration, not differences to document. The set grows as the
    #: adapter does.
    COMPARABLE = (
        "~cwp_workid_",
        "~cwp_work_",
        "~cwp_part_",
        "~cwp_part_levels",
        "~cwp_work_part_levels",
    )

    @classmethod
    def _tags_of(cls, metadata, want):
        """The tags to compare for one track.

        The tags of the track that the legacy wrote, and the ones the
        adapter wrote - so a level the adapter has and the legacy did
        not is compared too. Only the tags are compared: the fixture
        holds the ids the model gives under another key, and that is
        not a tag. The families the adapter does not write yet are
        left out - see COMPARABLE.
        """
        tags = {tag for tag in want if tag.startswith('~cwp_')}
        tags |= set(metadata)
        return sorted(
            tag for tag in tags
            if tag.startswith(('~cwp_workid_', '~cwp_work_', '~cwp_part_'))
            or tag in cls.COMPARABLE[3:])

    @staticmethod
    def _holds_more(got, wanted):
        """The adapter's level holds the legacy's ids, plus more.

        The model may keep more works in a level than the legacy did -
        both Swan Lake editions, say - so the level holds the legacy's
        ids **first**, in the same order, and the model's extra ones
        after them. The extra ids are the model's own decision and are
        checked by ``test_every_id_is_a_work_the_model_knows``.
        """
        if got is None or wanted is None:
            return False
        if not wanted:
            return False
        if len(got) < len(wanted):
            return False
        # the legacy's ids have to come first, in the same order
        return list(got[:len(wanted)]) == list(wanted)

    def test_the_ids_read_as_the_legacy_writes_them(self):
        """A level's ids read as the legacy's own tag reads.

        The legacy's tags were captured from Picard's metadata, so this
        compares the adapter's tags with the legacy's as Picard stores
        them: a single id is bare, and two are a list of two. The format
        is checked here, not only the ids.

        The tracks the model differs on are checked for the form only -
        their ids are the model's own, and they are checked against the
        recorded ones by ``test_the_differing_tracks_have_the_ids_the
        _model_gives``. So this test says nothing about the ids of
        those tracks; it says that they are written in the same shape.
        """
        for album, expected in sorted(self.legacy.items()):
            tags = _adapter(album).tags()
            differing = self.DIFFERING.get(album) or ()
            holding_more = self.HOLDING_MORE.get(album) or ()
            for label, want in sorted(expected['tracks'].items()):
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                metadata = track.as_metadata()
                # the tags are numbered by the levels that have ids, so
                # the numbers of the tags and of the levels are the same
                # only when no level is empty. Every level of every
                # fixture has ids, so they are the same - and the test
                # says so, rather than assuming it.
                self.assertTrue(
                    all(track.levels),
                    "%s %s: a level has no ids" % (album, label))
                for level, works in enumerate(track.levels):
                    tag = '~cwp_workid_%d' % level
                    legacy = want.get(tag)
                    stored = _as_picard_stores(metadata.get(tag))
                    if label in differing:
                        # the ids differ - and the model's level may hold
                        # more of them - but the form is the same: a
                        # tuple, like every other level
                        self.assertIsInstance(
                            metadata.get(tag), tuple,
                            "%s %s %s" % (album, label, tag))
                        continue
                    if legacy is None:
                        # the legacy wrote no such level, so the model's
                        # level is one of its own - and it has to be
                        # written as a tuple, like every other level
                        self.assertIsInstance(
                            metadata.get(tag), tuple,
                            "%s %s %s" % (album, label, tag))
                        continue
                    if label in holding_more:
                        # the model's level holds the legacy's ids and
                        # more, so the legacy's ids have to be the
                        # model's first ones
                        self.assertTrue(
                            _starts_with(stored, legacy),
                            "%s %s %s" % (album, label, tag))
                        continue
                    self.assertEqual(
                        stored, _as_picard_stores(legacy),
                        "%s %s %s" % (album, label, tag))

    def test_every_id_is_a_work_the_model_knows(self):
        """No id is invented.

        The model may hold more works in a level than the legacy did -
        both Swan Lake editions, say, and the Gayne scene level - but
        every id it writes has to be a work of the hierarchy.
        """
        for album in sorted(USE_PARTIAL):
            adapter = _adapter(album)
            hierarchy, _groups = adapter.build()
            known = set()
            for node in hierarchy.nodes.values():
                known |= set(node.works)
            for track in adapter.tags().values():
                for level in track.levels:
                    for work in level:
                        self.assertIn(
                            work, known,
                            "%s: %s is not a work of the hierarchy"
                            % (album, work.mbid))

    def test_the_work_group_is_the_legacys(self):
        """Every track's group is the legacy's group for it.

        The assignment of groups to tracks is pinned too - not just that
        every track has some group.
        """
        for album, expected in sorted(self.legacy.items()):
            tags = _adapter(album).tags()
            groups = set(expected['groups'])
            for label, want in sorted(expected['tracks'].items()):
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                self.assertIn(
                    track.as_metadata().get('~cwp_work_group'),
                    groups, album)
                self.assertEqual(
                    track.as_metadata().get('~cwp_work_group'),
                    want.get('~cwp_work_group'),
                    "%s %s" % (album, label))

    def test_the_deeper_tracks_really_are_deeper(self):
        """Every track in DEEPER really has a different level count.

        The list exempts a track from the level count check, so an entry
        that no longer needs the exemption - or never did - would let a
        regression through.

        And the difference has to be in the right direction: the
        model's chain of its own levels is *deeper* than the legacy's,
        never shallower. A count that was wrong in the other direction
        would satisfy a test that only looked for a difference.

        The depth of the whole hierarchy is a different thing - the
        model's top can be shallower than the legacy's - so it only has
        to differ.
        """
        for album, labels in self.DEEPER.items():
            tags = _adapter(album).tags()
            for label in labels:
                want = self.legacy[album]['tracks'][label]
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                metadata = track.as_metadata()
                counts = (
                    ('~cwp_part_levels', '~cwp_work_part_levels'))
                differs = any(
                    metadata.get(tag) != want.get(tag)
                    for tag in counts)
                self.assertTrue(
                    differs,
                    "%s %s is not deeper - it does not need the "
                    "exemption" % (album, label))
                # the track's own chain is never shallower
                got = metadata.get('~cwp_part_levels')
                expected = want.get('~cwp_part_levels')
                if got is not None and expected is not None:
                    self.assertGreaterEqual(
                        int(got), int(expected),
                        "%s %s: the model is shallower" % (album, label))

    def test_the_holding_more_tracks_really_hold_more(self):
        """Every track in HOLDING_MORE really holds more ids.

        The list exempts a track from the exact comparison, so an entry
        that no longer needs the exemption - or never did - would let a
        regression through.
        """
        for album, labels in self.HOLDING_MORE.items():
            tags = _adapter(album).tags()
            for label in labels:
                want = self.legacy[album]['tracks'][label]
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                metadata = track.as_metadata()
                holds_more = False
                for tag in metadata:
                    if not tag.startswith('~cwp_workid'):
                        continue
                    legacy = self._ids(want.get(tag))
                    got = self._ids(metadata.get(tag))
                    if legacy is None or got is None:
                        continue
                    if len(got) > len(legacy) and self._holds_more(
                            got, legacy):
                        holds_more = True
                self.assertTrue(
                    holds_more,
                    "%s %s does not hold more - it does not need the "
                    "exemption" % (album, label))

    def test_the_differing_tracks_differ_only_as_documented(self):
        """The tracks that differ differ in the documented way.

        A track may differ in the level ids, but its group and its top
        name are the legacy's - so a regression that broke those would
        show up here. The level counts differ for the Gayne tracks,
        which sit one level deeper, and for the two tracks whose top
        holds more works, which makes the tree under it deeper.
        """
        deeper = self.DEEPER
        for album, expected in sorted(self.legacy.items()):
            tags = _adapter(album).tags()
            for label, want in sorted(expected['tracks'].items()):
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                metadata = track.as_metadata()
                for tag in (
                        '~cwp_work_top',
                        '~cwp_work_group',
                        '~cwp_part_levels',
                        '~cwp_work_part_levels'):
                    if (tag in ('~cwp_part_levels',
                                '~cwp_work_part_levels')
                            and label in (deeper.get(album) or ())):
                        continue
                    self.assertEqual(
                        metadata.get(tag), want.get(tag),
                        "%s %s %s" % (album, label, tag))

    def test_the_differing_tracks_have_the_ids_the_model_gives(self):
        """The tracks that differ have the model's own ids.

        The legacy's ids are the model's with the model's own changes -
        so the model's ids are recorded in the fixture and checked here.
        Without this, a wrong id on one of these tracks would go
        unnoticed.
        """
        for album, labels in self.DIFFERING.items():
            tags = _adapter(album).tags()
            for label in labels:
                want = self.legacy[album]['tracks'][label].get('model')
                self.assertIsNotNone(
                    want, "%s %s has no recorded ids" % (album, label))
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                metadata = track.as_metadata()
                # the model wrote exactly these levels - an extra one
                # would be a regression, and a missing one too
                self.assertEqual(
                    {tag for tag in metadata
                     if tag.startswith('~cwp_workid')},
                    set(want),
                    "%s %s: the levels are not the recorded ones"
                    % (album, label))
                for tag, ids in sorted(want.items()):
                    self.assertEqual(
                        list(metadata.get(tag) or ()),
                        ids,
                        "%s %s %s" % (album, label, tag))

    def test_the_differing_tracks_are_present(self):
        """Every track of the legacy's is there - none was dropped."""
        for album, expected in sorted(self.legacy.items()):
            tags = _adapter(album).tags()
            for label in expected['tracks']:
                self.assertIn(
                    WorkId(_recording_id(album, label)), tags,
                    "%s %s is missing" % (album, label))


class StandInNamesTestCase(unittest.TestCase):
    """The name of a partly performed work's stand-in.

    A partly performed work is represented by the recording id in the
    first level, so a name looked up for that id would find nothing -
    WorkNames maps work ids to titles. The legacy resolves the name
    through the relation: the stand-in carries the title of the work the
    recording partly performs. The adapter merges those titles into its
    names at build time, so every level names itself the way the
    legacy's does.
    """

    def test_the_stand_in_takes_the_partly_performed_works_title(self):
        """daphnis d1t11 performs No. 7 in full and No. 6 in part;
        the stand-in at its first level names No. 6, as the legacy's
        recorded tags do."""
        adapter = _adapter("daphnis")
        rec = WorkId(_recording_id("daphnis", "d1t11"))
        track = adapter.tags()[rec]
        stand_in = track.levels[0][0]
        self.assertEqual(stand_in, rec)
        self.assertEqual(
            adapter.names.title_of(stand_in),
            "Daphnis et Chloé: Tableau I. No. 6. Nocturne. Danse lente "
            "et mystérieuse des Nymphes")

    def test_two_partly_performed_works_take_the_firsts_title(self):
        """One recording, two partly performed works: the stand-in is
        held once - the legacy de-duplicates its id list - and it names
        the first of them, in relation order."""
        recording = {
            "id": "rec-1",
            "title": "",
            "relations": [
                {"type": "performance", "work": {"id": "work-a",
                                                   "title": ""},
                 "attributes": ["partial"]},
                {"type": "performance", "work": {"id": "work-b",
                                                   "title": ""},
                 "attributes": ["partial"]},
            ],
        }
        names = WorkNames(titles={w("work-a"): "Title A",
                                  w("work-b"): "Title B"})
        adapter = AlbumAdapter([], [recording], names=names)
        self.assertEqual(
            adapter.recordings[0].members, [w("rec-1")],
            "the stand-in is held once")
        self.assertEqual(adapter.names.title_of(w("rec-1")), "Title A")

    def test_a_partly_performed_work_without_a_title_gives_no_name(self):
        """A partly performed work the build has no title for leaves the
        stand-in nameless - an empty title is no title, the same rule as
        any unknown work."""
        recording = {
            "id": "rec-1",
            "title": "",
            "relations": [
                {"type": "performance", "work": {"id": "work-a",
                                                   "title": ""},
                 "attributes": ["partial"]},
            ],
        }
        adapter = AlbumAdapter([], [recording], names=WorkNames())
        self.assertEqual(adapter.names.title_of(w("rec-1")), "")

    def test_a_complete_performance_takes_no_stand_in_title(self):
        """A work performed in full keeps its own id in the first level,
        and the recording id gains no title at all."""
        recording = {
            "id": "rec-1",
            "title": "",
            "relations": [
                {"type": "performance", "work": {"id": "work-a",
                                                   "title": ""}},
            ],
        }
        names = WorkNames(titles={w("work-a"): "Title A"})
        adapter = AlbumAdapter([], [recording], names=names)
        self.assertEqual(
            adapter.recordings[0].members, [w("work-a")])
        self.assertEqual(adapter.names.title_of(w("rec-1")), "")


class WorkNameTagsTestCase(unittest.TestCase):
    """The name tags: ~cwp_work_N, one per level of ids.

    The legacy writes the name of each level next to its ids - the
    tag whose number is the level's. A name the build does not
    know leaves the tag out: a tag is written only when it has a
    value, the same rule the id tags follow.
    """

    def _adapter_with_titles(self, works, recordings, titles):
        return AlbumAdapter(works, recordings,
                            names=WorkNames(titles=titles))

    def test_each_level_carries_its_name(self):
        """A three-level hierarchy: every level names itself."""
        works = [
            {"id": "work-top", "title": "",
             "relations": []},
            {"id": "work-mid", "title": "",
             "relations": [{"type": "parts", "direction": "backward", "work": {"id": "work-top"}}]},
            {"id": "work-leaf", "title": "",
             "relations": [{"type": "parts", "direction": "backward", "work": {"id": "work-mid"}}]},
        ]
        recording = {
            "id": "rec-1", "title": "",
            "relations": [
                {"type": "performance", "work": {"id": "work-leaf"}},
            ],
        }
        titles = {w("work-top"): "The Top",
                  w("work-mid"): "The Middle",
                  w("work-leaf"): "The Leaf"}
        adapter = self._adapter_with_titles(works, [recording], titles)
        metadata = adapter.tags()[w("rec-1")].as_metadata()
        self.assertEqual(metadata["~cwp_work_0"], "The Leaf")
        self.assertEqual(metadata["~cwp_work_1"], "The Middle")
        self.assertEqual(metadata["~cwp_work_2"], "The Top")

    def test_a_stand_in_carries_the_resolved_title(self):
        """daphnis d1t1: the first level is the recording standing in
        for the partly performed work - its name is that work's, as
        the recorded legacy tags say."""
        adapter = _adapter("daphnis")
        rec = WorkId(_recording_id("daphnis", "d1t1"))
        metadata = adapter.tags()[rec].as_metadata()
        self.assertEqual(
            metadata["~cwp_work_0"],
            "Daphnis et Chloé: Tableau I. No. 1. Introduction et danse "
            "religieuse")

    def test_several_works_at_a_level_several_names(self):
        """A level with two works holds both names - Picard stores a
        list, as the recorded legacy tags do for the same case."""
        adapter = _adapter("daphnis")
        rec = WorkId(_recording_id("daphnis", "d1t12"))
        metadata = adapter.tags()[rec].as_metadata()
        self.assertEqual(
            _as_picard_stores(metadata["~cwp_work_0"]),
            _as_picard_stores((
                "Daphnis et Chloé: Tableau II. No. 7. Introduction",
                "Daphnis et Chloé: Tableau II. No. 8. Danse guerrière")))

    def test_a_level_without_titles_gets_no_tag(self):
        """Works the build has no titles for name no level - and the
        other levels keep their own numbers, which are the id tags'
        numbers."""
        works = [
            {"id": "work-top", "title": "",
             "relations": []},
            {"id": "work-leaf", "title": "",
             "relations": [{"type": "parts", "direction": "backward", "work": {"id": "work-top"}}]},
        ]
        recording = {
            "id": "rec-1", "title": "",
            "relations": [
                {"type": "performance", "work": {"id": "work-leaf"}},
            ],
        }
        titles = {w("work-top"): "The Top"}  # the leaf has no title
        adapter = self._adapter_with_titles(works, [recording], titles)
        metadata = adapter.tags()[w("rec-1")].as_metadata()
        self.assertNotIn("~cwp_work_0", metadata)
        self.assertEqual(metadata["~cwp_work_1"], "The Top")


class PartNameTagsTestCase(unittest.TestCase):
    """The part tags: ~cwp_part_N, the level names with the level above out.

    The legacy writes each level's part next to its work name - the
    name of the work with the parent's name taken away. The top
    level, which has no parent, gets no part tag; a part whose name the
    strip empties is written empty, as the legacy writes it."""

    def test_each_level_but_the_top_carries_its_part(self):
        """daphnis d1t1: the recorded parts of the recorded works."""
        adapter = _adapter("daphnis")
        rec = WorkId(_recording_id("daphnis", "d1t1"))
        metadata = adapter.tags()[rec].as_metadata()
        self.assertEqual(metadata["~cwp_part_0"], "")
        self.assertEqual(metadata["~cwp_part_1"],
                         "No. 1. Introduction et danse religieuse")
        self.assertEqual(metadata["~cwp_part_2"], "Tableau I")
        self.assertNotIn("~cwp_part_3", metadata)

    def test_a_cyrillic_part_strips_the_same_way(self):
        """nutcracker d1t1: the part after the op. number - and the
        second part follows the documented extra id the model keeps."""
        adapter = _adapter("nutcracker")
        rec = WorkId(_recording_id("nutcracker", "d1t1"))
        metadata = adapter.tags()[rec].as_metadata()
        self.assertEqual(
            metadata["~cwp_part_0"],
            ("Увертюра", "I. Ouverture miniature. Allegro giusto"))

    def test_several_works_at_a_level_several_parts(self):
        """daphnis d1t12: each of the two works is stripped by itself."""
        adapter = _adapter("daphnis")
        rec = WorkId(_recording_id("daphnis", "d1t12"))
        metadata = adapter.tags()[rec].as_metadata()
        self.assertEqual(
            _as_picard_stores(metadata["~cwp_part_0"]),
            _as_picard_stores(("No. 7. Introduction",
                               "No. 8. Danse guerrière")))


class AdapterWithoutPicardTestCase(unittest.TestCase):
    """The adapter needs no Picard."""

    def test_no_module_of_the_package_imports_picard(self):
        """No source of the hierarchy package imports Picard.

        The whole package is scanned, subpackages included - a module
        that Picard reached would drag it in whatever imported it. The
        scan finds the plain imports; the test below blocks Picard at
        import time, which catches the ones written in other ways.
        """
        package = os.path.join(
            os.path.dirname(__file__), "..", "plugins", "classical_extras",
            "hierarchy")
        for path in self._sources(package):
            name = os.path.relpath(path, package)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            for line in source.splitlines():
                stripped = line.strip()
                self.assertFalse(
                    stripped.startswith("import picard")
                    or stripped.startswith("from picard"),
                    "%s imports Picard: %s" % (name, stripped))

    def test_the_package_does_not_import_picard(self):
        """Importing the package must not pull Picard in.

        The import is blocked, so a module that reached for Picard would
        fail rather than quietly work on a machine that has it.
        """
        import importlib

        blocked = []

        class PicardBlocked(ImportError):
            """The blocker refused to load Picard."""

        class Blocker:
            """A finder that refuses to load Picard."""

            def find_spec(self, name, path=None, target=None):
                if name == "picard" or name.startswith("picard."):
                    blocked.append(name)
                    raise PicardBlocked("Picard must not be imported")
                return None

        finder = Blocker()
        sys.meta_path.insert(0, finder)
        # Picard is removed from the module cache, so that an import of it
        # would really reach the blocker rather than find the cached one
        cached = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "picard" or name.startswith("picard.")}
        # the hierarchy modules are removed so that they are imported
        # afresh - and restored afterwards, so that the rest of the suite
        # sees the same classes
        hierarchy = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "hierarchy" or name.startswith("hierarchy.")}
        try:
            try:
                importlib.import_module("hierarchy.adapter")
            except PicardBlocked:
                # the blocker fired, so the import failed because of
                # Picard - which is what the test is about. The check
                # below says so.
                pass
            except ImportError:
                # the import failed for another reason - the package is
                # broken, not blocked. The error is raised, so that it
                # is not reported as an import of Picard.
                raise
        finally:
            sys.meta_path.remove(finder)
            sys.modules.update(cached)
            sys.modules.update(hierarchy)
            # a module the import brought in that was not there before
            # would shadow a later import, so it is removed
            for name in list(sys.modules):
                if (name == "hierarchy" or name.startswith("hierarchy.")) \
                        and name not in hierarchy:
                    del sys.modules[name]
        self.assertEqual(blocked, [], "the package imported Picard")

    def test_the_blocker_would_catch_an_import(self):
        """The blocker really blocks - otherwise the test above is void.

        A module that exists is blocked, so the failure comes from the
        blocker and not from the module not being there. The blocker
        records that it fired.
        """
        import importlib

        name = "json"
        fired = []

        class Blocker:
            """A finder that refuses to load the module."""

            def find_spec(self, fullname, path=None, target=None):
                if fullname == name:
                    fired.append(fullname)
                    raise ImportError("the module must not be imported")
                return None

        finder = Blocker()
        sys.meta_path.insert(0, finder)
        cached = sys.modules.pop(name, None)
        try:
            with self.assertRaises(ImportError):
                importlib.import_module(name)
        finally:
            sys.meta_path.remove(finder)
            if cached is not None:
                sys.modules[name] = cached
        self.assertEqual(fired, [name])

    @staticmethod
    def _sources(package):
        """Every python source of the package."""
        for root, _dirs, names in os.walk(package):
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(root, name)


if __name__ == "__main__":
    unittest.main()
