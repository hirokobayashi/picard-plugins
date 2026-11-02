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
    TrackTags,
    WorkNames,
    WorkRelations,
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
        is the order of the parents.
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
            "         'work': {'id': 'parent-a'}}]},\n"
            "    {'id': 'work-b', 'relations': [\n"
            "        {'type': 'parts', 'direction': 'backward',\n"
            "         'work': {'id': 'parent-b'}}]},\n"
            "    {'id': 'parent-a', 'title': 'The parent of A'},\n"
            "    {'id': 'parent-b', 'title': 'The parent of B'},\n"
            "])\n"
            "wanted = [WorkId('parent-a'), WorkId('parent-b')]\n"
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
            results, {"parent-a,parent-b"},
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
        """A tie keeps every tied name, in a fixed order.

        No fixture has a tie - the two Swan Lake editions share one
        title, so the names are one. So the tie is built here.

        The names are not in the order they would be sorted in, so that
        an implementation that kept the top's own order would not pass.
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
            top_names, ["The first work", "The second work"])

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

        This is the legacy's fallback when there is no vote information.
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
        """A cycle must not make the walk go on for ever.

        The node on the path is treated as a leaf, so ``a -> b -> a``
        has the height of ``a -> b``.
        """
        hierarchy = self._hierarchy({
            _node("a"): [_node("b")],
            _node("b"): [_node("a")],
        })
        self.assertEqual(node_height(hierarchy, _node("a")), 1)

    def test_a_self_loop_does_not_hang(self):
        hierarchy = self._hierarchy({
            _node("a"): [_node("a")],
        })
        self.assertEqual(node_height(hierarchy, _node("a")), 0)


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
        building.
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

        The recording is not that work, and does not stand in its place,
        so the work's parent is not in the level above.
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

        The recording is not that work, so it stands in for it nowhere
        and contributes nothing - so the work's own parent is not in the
        recording's chain either.
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
            # the work that is related twice is related partly, so the
            # recording stands in for it - but a work related once in
            # full is in the level as itself
            for work in works:
                partial = any(
                    relation["work"]["id"] == work
                    and 'partial' in (
                        relation.get("attributes") or ())
                    for relation in related)
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

    #: The tracks whose tags differ from the legacy's in more than extra
    #: ids, and why. The Gayne tracks sit one level deeper, because the
    #: model keeps the scene level; the Swan Lake tracks end at the
    #: original edition rather than the Drigo one, and their top holds
    #: both editions where the legacy's held one; and the Nutcracker,
    #: Planets and Requiem tracks have a top that holds more works than
    #: the legacy's.
    DIFFERING = {
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
        """
        differing = {}
        for album, expected in sorted(self.legacy.items()):
            tags = _adapter(album).tags()
            holding_more = self.HOLDING_MORE.get(album) or []
            for label, want in sorted(expected['tracks'].items()):
                track = tags.get(WorkId(_recording_id(album, label)))
                if track is None:
                    differing.setdefault(album, []).append(label)
                    continue
                metadata = track.as_metadata()
                for tag in self._tags_of(metadata, want):
                    if tag.startswith('~cwp_workid'):
                        got = self._ids(metadata.get(tag))
                        wanted = self._ids(want.get(tag))
                    else:
                        # the name and the group are text, not ids
                        got = metadata.get(tag)
                        wanted = want.get(tag)
                    if got == wanted:
                        continue
                    if (tag.startswith('~cwp_workid')
                            and label in holding_more
                            and self._holds_more(got, wanted)):
                        continue
                    differing.setdefault(album, []).append(label)
                    break
        self.assertEqual(
            differing, self.DIFFERING,
            "the tracks that differ from the legacy are not the "
            "documented ones")

    @staticmethod
    def _tags_of(metadata, want):
        """The tags to compare for one track.

        The legacy's own tags for the track, and the adapter's - so a
        level the adapter has and the legacy did not is compared too.
        Only the tags are compared: the fixture holds the model's own
        ids under another key, and that is not a tag.
        """
        tags = {tag for tag in want if tag.startswith('~cwp_')}
        tags |= set(metadata)
        return sorted(tags)

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
        """
        for album, labels in self.DEEPER.items():
            tags = _adapter(album).tags()
            for label in labels:
                want = self.legacy[album]['tracks'][label]
                track = tags.get(WorkId(_recording_id(album, label)))
                self.assertIsNotNone(
                    track, "%s %s has no tags" % (album, label))
                metadata = track.as_metadata()
                differs = any(
                    metadata.get(tag) != want.get(tag)
                    for tag in (
                        '~cwp_part_levels', '~cwp_work_part_levels'))
                self.assertTrue(
                    differs,
                    "%s %s is not deeper - it does not need the "
                    "exemption" % (album, label))

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

        class Blocker:
            """A finder that refuses to load Picard."""

            def find_spec(self, name, path=None, target=None):
                if name == "picard" or name.startswith("picard."):
                    blocked.append(name)
                    raise ImportError("Picard must not be imported")
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
            except ImportError:
                if not blocked:
                    # the import failed for another reason - the package
                    # is broken, not blocked
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
