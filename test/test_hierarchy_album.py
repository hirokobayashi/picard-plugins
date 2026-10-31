#!/usr/bin/env python
# coding: utf-8
"""Tests for the album level model: a recording's performance and its group.

These cover the two pieces the work hierarchy alone cannot express:

* a recording that performs several works has one chain, not one per work;
* the group - the shuffle key - is one name, chosen by a vote over the
  album, so every track of an album yields the identical string.
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

from hierarchy.album import (  # noqa: E402
    Album,
    Performance,
    candidate_tops,
    chains_of,
    normalise_name,
)
from hierarchy.graph import WorkGraph, WorkId  # noqa: E402
from hierarchy.project import project  # noqa: E402


def w(mbid):
    return WorkId(mbid)


def _performance_of(rows, label):
    """The performance of the named track."""
    for row_label, performance in rows:
        if row_label == label:
            return performance
    raise AssertionError("no track %r" % label)


class PerformanceTestCase(unittest.TestCase):
    """What a recording's performance node holds."""

    def test_a_work_performed_in_full_stands_for_itself(self):
        performance = Performance(
            key=w("recording"), works=frozenset({w("work")}),
            complete=frozenset({w("work")}))
        self.assertEqual(performance.members, frozenset({w("work")}))
        self.assertEqual(performance.contribution(w("work")), "parents")

    def test_a_work_performed_only_in_part_stands_for_the_recording(self):
        """The recording is not the work, so the recording id stands in."""
        performance = Performance(
            key=w("recording"), works=frozenset({w("work")}),
            partial=frozenset({w("work")}))
        self.assertEqual(performance.members, frozenset({w("recording")}))
        self.assertEqual(performance.contribution(w("work")), "none")

    def test_a_work_performed_both_ways_stands_for_the_recording(self):
        """Daphnis' tracks carry a 'partial' relation and a plain one, both
        pointing at the same work. The recording stands in for the work, and
        the work itself sits one level above."""
        performance = Performance(
            key=w("recording"), works=frozenset({w("work")}),
            partial=frozenset({w("work")}),
            complete=frozenset({w("work")}))
        self.assertEqual(performance.members, frozenset({w("recording")}))
        self.assertEqual(performance.contribution(w("work")), "self")

    def test_several_works_form_one_node(self):
        performance = Performance(
            key=w("recording"),
            works=frozenset({w("first"), w("second")}),
            complete=frozenset({w("first"), w("second")}))
        self.assertEqual(
            performance.members, frozenset({w("first"), w("second")}))

    def test_a_partial_work_must_be_performed(self):
        with self.assertRaises(ValueError):
            Performance(
                key=w("recording"), works=frozenset(),
                partial=frozenset({w("work")}))

    def test_without_partial_ignores_the_attribute(self):
        """The 'partial' attribute only means anything with the plugin's
        cwp_partial option on. With it off the work is an ordinary one."""
        performance = Performance(
            key=w("recording"), works=frozenset({w("work")}),
            partial=frozenset({w("work")}))
        plain = performance.without_partial()
        self.assertEqual(plain.members, frozenset({w("work")}))
        self.assertEqual(plain.contribution(w("work")), "parents")
        self.assertEqual(plain.partial, frozenset())


class GroupNameTestCase(unittest.TestCase):
    """The shuffle key of every track."""

    def test_one_candidate_name_is_the_group(self):
        album = Album(names={w("top"): "Swan Lake, op. 20"})
        groups = album.group_names({w("recording"): frozenset({w("top")})})
        self.assertEqual(groups[w("recording")], "Swan Lake, op. 20")

    def test_several_candidate_names_take_the_vote_winner(self):
        """Every track votes for each of its own candidate names, and the
        name the album prefers wins."""
        album = Album(names={
            w("top1"): "The Planets, op. 32",
            w("top2"): "The Planets Suite extension",
        })
        candidates = {
            w("rec1"): frozenset({w("top1"), w("top2")}),
            w("rec2"): frozenset({w("top1")}),
            w("rec3"): frozenset({w("top1")}),
        }
        groups = album.group_names(candidates)
        self.assertEqual(groups[w("rec1")], "The Planets, op. 32")
        self.assertEqual(groups[w("rec2")], "The Planets, op. 32")
        self.assertEqual(groups[w("rec3")], "The Planets, op. 32")

    def test_a_track_without_candidates_has_no_group(self):
        album = Album(names={})
        groups = album.group_names({w("recording"): frozenset()})
        self.assertEqual(groups[w("recording")], "")

    def test_a_tie_is_broken_by_the_lowest_normalised_name(self):
        """A tie must still give every track of the album the identical
        string, or the album splits into several shuffle groups."""
        album = Album(names={
            w("top1"): "Beta",
            w("top2"): "Alpha",
        })
        candidates = {w("recording"): frozenset({w("top1"), w("top2")})}
        groups = album.group_names(candidates)
        self.assertEqual(groups[w("recording")], "Alpha")

    def test_the_same_name_counts_once_per_track(self):
        """Two candidate works with the same name are one candidate, so the
        track does not vote for it twice."""
        album = Album(names={
            w("top1"): "Swan Lake, op. 20",
            w("top2"): "Swan Lake, op. 20",
        })
        candidates = {
            w("rec1"): frozenset({w("top1"), w("top2")}),
            w("rec2"): frozenset({w("top1")}),
        }
        groups = album.group_names(candidates)
        self.assertEqual(groups[w("rec1")], "Swan Lake, op. 20")
        self.assertEqual(groups[w("rec2")], "Swan Lake, op. 20")

    def test_normalise_name_matches_the_legacy(self):
        self.assertEqual(normalise_name("  Swan   Lake, op. 20 "),
                         "swan lake, op. 20")
        self.assertEqual(normalise_name('"Quoted".'), "quoted")
        self.assertEqual(normalise_name(None), "")


class AlbumFixtureTestCase(unittest.TestCase):
    """The album model applied to the real MusicBrainz fixtures."""

    FIXTURE_ROOT = os.path.join(
        os.path.dirname(__file__), "fixtures")

    def _build(self, fixdir):
        graph = WorkGraph()
        names = {}
        for path in glob.glob(os.path.join(fixdir, "work_*.json")):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            source = WorkId(data["id"])
            names[source.mbid] = data.get("title")
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
        return graph, names

    def _performances(self, fixdir, use_partial=True):
        """The recordings of the fixture and the works they perform."""
        performances = []
        for path in sorted(glob.glob(os.path.join(fixdir, "rec_*.json"))):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            works = set()
            partial = set()
            complete = set()
            for relation in data.get("relations", []):
                target = relation.get("work")
                if not target or not target.get("id"):
                    continue
                work = WorkId(target["id"])
                works.add(work)
                if "partial" in (relation.get("attributes") or ()):
                    partial.add(work)
                else:
                    complete.add(work)
            performance = Performance(
                key=WorkId(data["id"]),
                works=frozenset(works),
                partial=frozenset(partial),
                complete=frozenset(complete))
            if not use_partial:
                performance = performance.without_partial()
            performances.append((os.path.basename(path)[4:-5], performance))
        return performances

    #: The albums whose legacy test runs with the cwp_partial option on.
    #: With it off the 'partial' attribute is ignored, which is what the
    #: Gayne and Planets albums' tags show.
    PARTIAL_ON = ("daphnis", "firebird", "nutcracker", "requiem",
                  "swanlake_full", "swanlake_jarvi")

    def _album(self, fixdir, use_partial=True):
        graph, names = self._build(fixdir)
        rows = self._performances(fixdir, use_partial=use_partial)
        performances = [performance for _label, performance in rows]
        hierarchy = project(graph, performances=performances)
        chains = chains_of(hierarchy, performances)
        album = Album(names={WorkId(k): v for k, v in names.items()})
        groups = album.group_names(candidate_tops(chains, hierarchy))
        return rows, hierarchy, chains, groups

    def _album_of(self, fixture):
        """The album of a fixture, with the option its legacy test uses."""
        fixdir = os.path.join(self.FIXTURE_ROOT, fixture)
        if not os.path.isdir(fixdir):
            self.skipTest("no %s fixture" % fixture)
        return self._album(
            fixdir, use_partial=fixture in self.PARTIAL_ON)

    def test_daphnis_the_same_work_twice_gives_the_recording_id(self):
        """Daphnis' first track is related to one work twice: a 'partial'
        performance relation and a plain one. The recording is not the work,
        so the recording id stands in for it at level 0 and the work sits one
        level above."""
        fixdir = os.path.join(self.FIXTURE_ROOT, "daphnis")
        rows, _hierarchy, chains, _groups = self._album(fixdir)
        chain = chains[_performance_of(rows, "d1t1").key]
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in chain],
            [["03e53458-39f8-482a-b470-80a5a70d15bf"],
             ["210c1890-db08-324d-a0b4-fc09c388152f"],
             ["73dd63b8-543f-4725-b643-ed384fe5dcac"],
             ["a55d2dfe-fb81-42e8-9e0f-3e6fd9654ca1"]])

    def test_daphnis_two_works_form_one_node(self):
        """Daphnis' track 6 performs two different works. They are peers, so
        they form one node and the track has one chain."""
        fixdir = os.path.join(self.FIXTURE_ROOT, "daphnis")
        rows, _hierarchy, chains, _groups = self._album(fixdir)
        chain = chains[_performance_of(rows, "d1t6").key]
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in chain],
            [["54d756d9-1391-4710-a134-29c535fa4305",
              "70e3948f-db7c-3b31-95e4-70baa564a393"],
             ["73dd63b8-543f-4725-b643-ed384fe5dcac"],
             ["a55d2dfe-fb81-42e8-9e0f-3e6fd9654ca1"]])

    def test_planets_the_two_neptunes_form_one_node(self):
        """Planets' track 7 performs both suites' "Neptune". One node, one
        chain - and the group is the vote winner among its candidates."""
        fixdir = os.path.join(self.FIXTURE_ROOT, "planets")
        rows, _hierarchy, chains, groups = self._album(fixdir)
        key = _performance_of(rows, "t7").key
        chain = chains[key]
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in chain],
            [["254f397b-0d15-4812-aec4-02d0aa1d5898",
              "4be2ad86-562d-4ef0-970a-cebf777fb8cb"],
             ["439c1605-bf74-4e0c-b2d9-6f4f89619ec6",
              "5a75e61c-a670-37a9-bf73-4e7e22bbb12a"]])
        self.assertEqual(groups[key], "The Planets, op. 32")

    def test_gayne_the_scene_level_is_kept(self):
        """The movements of "Act I, Scene I (Spring)" are listed under both
        the scene and the act. The scene is itself a part of the act, so the
        act is an ancestor of the scene and the reduction drops the
        ancestor: the scene, the more specific placement, is kept.

        The legacy flattens these to the act, because it never resolved the
        scene's own chain and so could not see the scene sits under the act.
        """
        _rows, _hierarchy, chains, _groups = self._album_of("gayne")
        rows = self._performances(
            os.path.join(self.FIXTURE_ROOT, "gayne"), use_partial=False)
        key = _performance_of(rows, "d1t10").key
        chain = chains[key]
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in chain],
            [["fb479c25-21be-4fba-aaa5-78693fcaafdd"],
             ["697f1f18-1a3c-4981-85ee-eb2c43e9c971"],
             ["9ffd930d-eb52-4c0a-8ee4-551a5d7c32ad"],
             ["693e681e-98a1-4d21-9bc8-e084a248461d"]])

    def test_don_giovanni_the_standalone_parent_is_dropped(self):
        """"Don Giovanni: Recitative and Aria, K. 540c" is a standalone work
        and also a part of the opera's second act. The act is embedded in
        the opera and the standalone work is not, so the standalone parent
        is the redundant one and is dropped.

        This is the reduction at the level of a performance's parent: the
        recording's works contribute two parents, and the reduction picks
        the one that fits the hierarchy.
        """
        _rows, _hierarchy, chains, _groups = self._album_of("dongiovanni")
        rows = self._performances(
            os.path.join(self.FIXTURE_ROOT, "dongiovanni"), use_partial=False)
        key = _performance_of(rows, "d6t3").key
        chain = chains[key]
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in chain],
            [["c1b48770-4e0c-3b69-9e6a-6f6f01da863d"],
             ["18a0544b-89c1-4510-a673-c1b1728fd742"],
             ["b3b1e2b3-cbb8-4b46-a7d0-0031ec13492c"]])

    def test_the_performance_parent_set_is_reduced(self):
        """Two works that contribute conflicting parents.

        The work level reduction handles each work's own parents, but the
        *union* of two works' parents can still conflict: here one work sits
        under the opera and the other under the opera's act, so the act is an
        ancestor of the opera and the act is the redundant one.

        No fixture reaches this - the work level reduction has already dealt
        with their cases - so it is built by hand.
        """
        graph = WorkGraph()
        opera = w("opera")
        act = w("act")
        first = w("first")
        second = w("second")
        for work in (opera, act, first, second):
            graph.add_work(work)
        graph.add_parent_edge(act, opera)
        graph.add_parent_edge(first, opera)
        graph.add_parent_edge(second, act)

        performance = Performance(
            key=w("recording"),
            works=frozenset({first, second}),
            complete=frozenset({first, second}))
        hierarchy = project(graph, performances=[performance])
        chain = hierarchy.path_to_top(hierarchy.node_of_performance[w("recording")])
        self.assertEqual(
            [sorted(work.mbid for work in node.works) for node in chain],
            [["first", "second"], ["act"], ["opera"]])

    def test_the_group_is_one_value_for_every_track_of_an_album(self):
        """The group is the shuffle key: every track of an album must carry
        the identical string, or the album is split into several groups."""
        for fixture, expected in (
                ("swanlake_full", 1),
                ("swanlake_jarvi", 1),
                ("gayne", 1),
                ("requiem", 1),
                ("nutcracker", 1),
                ("firebird", 3),
                ("tristan", 6),
                ("planets", 6)):
            _rows, _hierarchy, _chains, groups = self._album_of(fixture)
            self.assertEqual(
                len(set(groups.values())), expected,
                "%s: %d groups, expected %d"
                % (fixture, len(set(groups.values())), expected))

    def test_the_swan_lake_albums_share_one_group(self):
        """The two albums are the same ballet, so they share a group even
        though the work ids differ."""
        groups = []
        for fixture in ("swanlake_full", "swanlake_jarvi"):
            _rows, _hierarchy, _chains, album_groups = self._album_of(fixture)
            self.assertEqual(set(album_groups.values()), {"Swan Lake, op. 20"})
            groups.append(frozenset(album_groups.values()))
        self.assertEqual(len(set(groups)), 1)

    def test_a_track_of_one_album_does_not_change_another_album(self):
        """The vote is over the album, so the same recording in two albums
        of one work must still land in one group each."""
        _rows, _hierarchy, _chains, full_groups = self._album_of(
            "swanlake_full")
        _rows, _hierarchy, _chains, jarvi_groups = self._album_of(
            "swanlake_jarvi")
        self.assertEqual(set(full_groups.values()), {"Swan Lake, op. 20"})
        self.assertEqual(set(jarvi_groups.values()), {"Swan Lake, op. 20"})


if __name__ == "__main__":
    unittest.main()
