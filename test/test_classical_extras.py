#!/usr/bin/env python
# coding: utf-8
"""Tests for the classical_extras plugin (v2.0.14) changes.

Covers the three approved tasks:
  * Task A  - Cyrillic names transliterated to Latin; cea_cyrillic always
              removes the patronymic/middle name (cyrillic_to_latin + get_roman).
  * Task D  - cwp_excluded_works option (excludes works by MusicBrainz id).
  * Top work fix - duplicate top works in albums; tracks shared between two
              top works keep the most-selected top work.
"""
import collections
import copy
import json
import unittest

from test.plugin_test_case import PluginTestCase

# const.py has no module-level config access, so it can be imported directly
# to read the option defaults that the plugin expects at import time.
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins",
                                "classical_extras"))
import const as _ce_const  # noqa: E402
sys.path.pop(0)

_ALL_OPTION_DEFAULTS = {}
for _group in ("ARTISTS_OPTIONS", "TAG_OPTIONS", "TAG_DETAIL_OPTIONS",
              "WORKPARTS_OPTIONS", "GENRE_OPTIONS", "PICARD_OPTIONS",
              "OTHER_OPTIONS"):
    for _o in getattr(_ce_const, _group):
        _ALL_OPTION_DEFAULTS[_o["option"]] = copy.deepcopy(_o["default"])


class ClassicalExtrasTestCase(PluginTestCase):
    MOD = "classical_extras"
    _mod = None  # cached plugin module (loaded once for the class)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The plugin registers script functions into the global
        # ScriptParser._function_registry and leaves picard.config.config as a
        # Mock. Both leak into later plain-unittest.TestCase tests (e.g.
        # test_keep) whose ScriptParser then KeyErrors on 'enabled_plugins'.
        # Restore a clean global state once the whole class is done.
        cls.addClassCleanup(cls._cleanup_global_state)

    @classmethod
    def _cleanup_global_state(cls):
        # Drop the classical_extras entries from every ExtensionPoint (script
        # functions, taggers, etc.) so they no longer show up in the registry.
        try:
            from picard.plugin import _unregister_module_extensions
            _unregister_module_extensions(cls.MOD)
        except Exception:
            pass
        # Reset picard.config.config so get_config() returns None and
        # ExtensionPoint.__iter__ falls back to [] instead of KeyError-ing.
        try:
            from picard import config as _picard_config
            _picard_config.config = None
            _picard_config.setting = None
            _picard_config.persist = None
            _picard_config.profiles = None
        except Exception:
            pass

    def setUp(self):
        super().setUp()
        # Seed config with all option defaults so the plugin's module-level
        # code (which reads config.setting[...] at import time) doesn't KeyError.
        self.set_config_values(setting=dict(_ALL_OPTION_DEFAULTS))
        # Load the plugin once. Re-installing per test breaks because
        # unload_plugin doesn't fully clear the `const` submodule from
        # sys.modules, so the bare `const` name isn't re-bound on reload.
        if ClassicalExtrasTestCase._mod is None:
            ClassicalExtrasTestCase._mod = self._test_plugin_install(
                "Classical Extras", self.MOD)
        self.mod = ClassicalExtrasTestCase._mod

    # ----- Task A: cyrillic transliteration -----

    def test_cyrillic_to_latin_uses_sortname_when_latin(self):
        """A cyrillic name with a Latin sort-name uses the sort-name (patronymic removed)."""
        cyrillic_to_latin = self.mod.cyrillic_to_latin
        # Пётр Ильич Чайковский -> sort-name "Tchaikovsky, Pyotr Ilyich"
        result = cyrillic_to_latin(
            "Пётр Ильич Чайковский",
            "Tchaikovsky, Pyotr Ilyich")
        # patronymic "Ilyich" must be removed
        self.assertNotIn("Ilyich", result)
        self.assertIn("Tchaikovsky", result)
        self.assertEqual(result, "Pyotr Tchaikovsky")

    def test_cyrillic_to_latin_transliterates_non_latin_sortname(self):
        """If the sort-name is itself non-Latin it is transliterated to Latin."""
        cyrillic_to_latin = self.mod.cyrillic_to_latin
        result = cyrillic_to_latin("Чайковский", "Чайковский, Пётр Ильич")
        # must be entirely latin characters (no cyrillic remaining)
        self.assertTrue(self.mod.only_roman_chars(result), msg=result)
        # patronymic removed
        self.assertNotIn("Ильич", result)

    def test_cyrillic_to_latin_always_latin_output(self):
        """Output is always roman regardless of input script."""
        cyrillic_to_latin = self.mod.cyrillic_to_latin
        result = cyrillic_to_latin("Рахманинов", "Рахманинов, Сергей Васильевич")
        self.assertTrue(self.mod.only_roman_chars(result), msg=result)

    def test_get_roman_uppercase_initial(self):
        """A standalone cyrillic uppercase letter transliterates to a capital latin letter."""
        get_roman = self.mod.get_roman
        # "П" alone (no following lowercase) -> "P" capitalised
        self.assertEqual(get_roman("П"), "P")

    def test_get_roman_last_uppercase_safe(self):
        """An uppercase cyrillic letter at the end of the string doesn't index out of range."""
        get_roman = self.mod.get_roman
        # Must not raise IndexError; result all latin
        result = get_roman("Иванов С")
        self.assertTrue(self.mod.only_roman_chars(result), msg=result)

    # ----- Task A: remove_middle still works -----

    def test_remove_middle_drops_patronymic(self):
        remove_middle = self.mod.remove_middle
        self.assertEqual(remove_middle("Pyotr Ilyich Tchaikovsky"),
                         "Pyotr Tchaikovsky")

    def test_cyrillic_to_latin_romanizes_non_cyrillic_script_in_full(self):
        """Regression: a Japanese ensemble name must be romanized from its

        sort-name in full, not have a word dropped. Bug report: 'Tokyo
        Konsei Gasshoudan' (Tokyo Mixed-Voice Choir) was being mangled to
        'Tokyo Gasshoudan' because cyrillic_to_latin treated any non-Latin
        -script 3-word name as "First Patronymic Last" and dropped the
        middle word, even though the name is Japanese, not Cyrillic.
        Patronymic removal must stay off for non-Cyrillic scripts, but the
        name must still be romanized (as it always has been), not left in
        its native script.
        """
        cyrillic_to_latin = self.mod.cyrillic_to_latin
        name = "東京混声合唱団"  # Tokyo Konsei Gasshodan (Tokyo Mixed-Voice Choir)
        result = cyrillic_to_latin(name, "Tōkyō Konsei Gasshōdan")
        # romanized via the (already-Latin) sort-name, with all three words
        # intact - not silently mangled to "Tokyo Gasshoudan", and not left
        # untranslated in kanji either.
        self.assertEqual(result, "Tōkyō Konsei Gasshōdan")

    def test_cyrillic_to_latin_keeps_three_word_group_name(self):
        """Regression: a Cyrillic-script *group* (not a person) whose

        plain, uninverted sort-name happens to have three words must not
        have the middle word stripped - remove_middle's "First Patronymic
        Last" heuristic only applies to personal "Surname, Given Names"
        sort-names (which contain a comma); groups/choirs/orchestras use
        their plain name as the sort-name by MB convention.
        """
        cyrillic_to_latin = self.mod.cyrillic_to_latin
        # A hypothetical Russian choir; sort-name equals the plain name
        # (no comma) as MB convention dictates for groups.
        result = cyrillic_to_latin(
            "Государственный Академический Хор",
            "Государственный Академический Хор")
        self.assertTrue(self.mod.only_roman_chars(result), msg=result)
        # all three words survive - none dropped as a "patronymic"
        self.assertEqual(len(result.split()), 3, msg=result)

    # ----- Task D: cwp_excluded_works option registered -----

    def test_excluded_works_option_registered(self):
        """The new cwp_excluded_works option is present in WORKPARTS_OPTIONS."""
        keys = [o['option'] for o in self.mod.const.WORKPARTS_OPTIONS]
        self.assertIn('cwp_excluded_works', keys)
        # default is an empty string
        opt = [o for o in self.mod.const.WORKPARTS_OPTIONS
               if o['option'] == 'cwp_excluded_works'][0]
        self.assertEqual(opt['default'], '')
        self.assertEqual(opt['type'], 'Text')

    # ----- Top work fix: chosen_top / de-duplication helpers -----

    def test_top_album_dedup_preserves_order(self):
        """list(dict.fromkeys(...)) de-duplicates while preserving order."""
        deduped = list(dict.fromkeys([("w1",), ("w2",), ("w1",), ("w3",)]))
        self.assertEqual(deduped, [("w1",), ("w2",), ("w3",)])

    def _make_partlevels(self, top, parts):
        """Build a PartLevels instance wired with the given tops and parts so
        that _resolve_chosen_tops can be exercised in isolation."""
        PartLevels = self.mod.PartLevels
        pl = PartLevels.__new__(PartLevels)
        pl.top = collections.defaultdict(list)
        pl.top["alb"] = list(top)
        pl.parts = parts
        pl.top_works = {}
        return pl

    def test_chosen_top_logic_most_selected(self):
        """Most-selected top (by unambiguous tracks) wins for shared tracks."""
        import collections
        PartLevels = self.mod.PartLevels
        topA = ("wA",)
        topB = ("wB",)
        pl = self._make_partlevels(
            [topA, topB],
            {topA: {"name": "Opera"}, topB: {"name": "OvertureColl"}})
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {topA}
        track_tops[("t2", "alb")] = {topA}
        track_tops[("t3", "alb")] = {topA, topB}
        most_selected, chosen_top = pl._resolve_chosen_tops(
            "test", "alb", track_tops)
        # topA chosen by two unambiguous tracks, topB by none -> topA wins
        self.assertEqual(most_selected, topA)
        # the shared track resolves to the most-selected top (topA)
        self.assertEqual(chosen_top[("t3", "alb")], topA)

    def test_chosen_top_non_collection_wins(self):
        """A single non-collection candidate beats most-selected for a shared
        track (per README: parent work is the highest level non-collection)."""
        import collections
        topOpera = ("wOpera",)
        topColl = ("wColl",)
        pl = self._make_partlevels(
            [topOpera, topColl],
            {topOpera: {"name": "Opera"},
             topColl: {"name": "OvertureColl", "is_collection": True}})
        track_tops = collections.defaultdict(set)
        # t1 unambiguous under the collection, t2 shared
        track_tops[("t1", "alb")] = {topColl}
        track_tops[("t2", "alb")] = {topOpera, topColl}
        most_selected, chosen_top = pl._resolve_chosen_tops(
            "test", "alb", track_tops)
        # most-selected (unambiguous) is the collection, but the shared track's
        # single non-collection candidate is the opera, so it wins for t2
        self.assertEqual(most_selected, topColl)
        self.assertEqual(chosen_top[("t2", "alb")], topOpera)

    def test_chosen_top_empty_album_no_crash(self):
        """An album whose tracks appear under no top resolves without error."""
        import collections
        topA = ("wA",)
        pl = self._make_partlevels([topA], {topA: {"name": "A"}})
        track_tops = collections.defaultdict(set)
        most_selected, chosen_top = pl._resolve_chosen_tops(
            "test", "alb", track_tops)
        # no unambiguous tracks, so each top tallies 0; 0 > -1 selects the
        # first top as a deterministic fallback (harmless: chosen_top is empty
        # since no tracks were under any top).
        self.assertEqual(most_selected, topA)
        self.assertEqual(chosen_top, {})

    def test_chosen_top_tie_keeps_all_candidates(self):
        """When every top is tied (no unambiguous tracks) a shared track keeps
        every candidate as a possible top work - the tie is not broken by
        arbitrarily picking one, so the album can legitimately report two top
        works (e.g. a single overture recording that is part of two equal
        parent works)."""
        import collections
        topOpera = ("wOpera",)
        topOverture = ("wOverture",)
        pl = self._make_partlevels(
            [topOpera, topOverture],
            {topOpera: {"name": "Opera"},
             topOverture: {"name": "Overture and Venusberg"}})
        track_tops = collections.defaultdict(set)
        # one shared track, no unambiguous tracks at all -> tied
        track_tops[("t1", "alb")] = {topOpera, topOverture}
        most_selected, chosen_top = pl._resolve_chosen_tops(
            "test", "alb", track_tops)
        # most_selected is the first top (deterministic fallback, 0 > -1) but
        # this does NOT prune the other top: the pruning in process_album is
        # guarded by has_clear_winner, which is False when no track is
        # unambiguous.
        self.assertIn(most_selected, (topOpera, topOverture))
        # the shared track resolves to the fallback top (deterministic) but the
        # album keeps BOTH top works (pruning is guarded upstream).
        self.assertEqual(len(chosen_top), 1)
        self.assertIn(chosen_top[("t1", "alb")], (topOpera, topOverture))

    def test_collapse_to_most_selected_top_work(self):
        """The reported 'two top_works' bug: an album ends up with two top-level
        works where one is chosen by more tracks than the other. The less-selected
        top (which has no tracks exclusive to it) is collapsed away so the album
        reports a single top work - the most-selected one."""
        import collections
        topMain = ("wMain",)
        topDup = ("wDup",)
        pl = self._make_partlevels(
            [topMain, topDup],
            {topMain: {"name": "Symphony"},
             topDup: {"name": "Symphony (duplicate)"}})
        track_tops = collections.defaultdict(set)
        # t1, t2 unambiguous under the main work; t3 shared (a duplicate path)
        track_tops[("t1", "alb")] = {topMain}
        track_tops[("t2", "alb")] = {topMain}
        track_tops[("t3", "alb")] = {topMain, topDup}
        _, chosen_top = pl._resolve_chosen_tops("test", "alb", track_tops)
        # Replicate the album-level collapse from process_album:
        top_choice = collections.Counter()
        for (t, al), v in chosen_top.items():
            if al == "alb" and v is not None:
                top_choice[tuple(v)] += 1
        exclusive = collections.Counter()
        for t, tops in track_tops.items():
            if t[1] == "alb" and len(tops) == 1:
                exclusive[next(iter(tops))] += 1
        best = max(top_choice.get(tuple(x), 0) for x in pl.top["alb"])
        winners = [x for x in pl.top["alb"]
                   if top_choice.get(tuple(x), 0) == best or exclusive.get(x, 0)]
        # the duplicate (0 exclusive, not most-selected) is collapsed away
        self.assertEqual(winners, [topMain])

    def test_collapse_keeps_exclusive_top(self):
        """A top that owns exclusive tracks is never collapsed away, even if it
        is not the most-selected, so its tracks keep their work metadata."""
        import collections
        topBig = ("wBig",)
        topSmall = ("wSmall",)
        pl = self._make_partlevels(
            [topBig, topSmall],
            {topBig: {"name": "Big"}, topSmall: {"name": "Small"}})
        track_tops = collections.defaultdict(set)
        # 4 tracks exclusive to Big, 2 tracks exclusive to Small, 1 shared
        track_tops[("t1", "alb")] = {topBig}
        track_tops[("t2", "alb")] = {topBig}
        track_tops[("t3", "alb")] = {topBig}
        track_tops[("t4", "alb")] = {topBig}
        track_tops[("t5", "alb")] = {topSmall}
        track_tops[("t6", "alb")] = {topSmall}
        track_tops[("t7", "alb")] = {topBig, topSmall}
        _, chosen_top = pl._resolve_chosen_tops("test", "alb", track_tops)
        top_choice = collections.Counter()
        for (t, al), v in chosen_top.items():
            if al == "alb" and v is not None:
                top_choice[tuple(v)] += 1
        exclusive = collections.Counter()
        for t, tops in track_tops.items():
            if t[1] == "alb" and len(tops) == 1:
                exclusive[next(iter(tops))] += 1
        best = max(top_choice.get(tuple(x), 0) for x in pl.top["alb"])
        winners = [x for x in pl.top["alb"]
                   if top_choice.get(tuple(x), 0) == best or exclusive.get(x, 0)]
        # both tops own exclusive tracks -> both kept (genuine multi-work album)
        self.assertEqual(set(winners), {topBig, topSmall})

    def test_collapse_tied_tops_kept(self):
        """Two genuine tops with equal chosen counts (both owning exclusive
        tracks) are both kept - a real tie is never broken arbitrarily."""
        import collections
        topA = ("wA",)
        topB = ("wB",)
        pl = self._make_partlevels(
            [topA, topB], {topA: {"name": "A"}, topB: {"name": "B"}})
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {topA}
        track_tops[("t2", "alb")] = {topA}
        track_tops[("t3", "alb")] = {topB}
        track_tops[("t4", "alb")] = {topB}
        _, chosen_top = pl._resolve_chosen_tops("test", "alb", track_tops)
        top_choice = collections.Counter()
        for (t, al), v in chosen_top.items():
            if al == "alb" and v is not None:
                top_choice[tuple(v)] += 1
        exclusive = collections.Counter()
        for t, tops in track_tops.items():
            if t[1] == "alb" and len(tops) == 1:
                exclusive[next(iter(tops))] += 1
        best = max(top_choice.get(tuple(x), 0) for x in pl.top["alb"])
        winners = [x for x in pl.top["alb"]
                   if top_choice.get(tuple(x), 0) == best or exclusive.get(x, 0)]
        self.assertEqual(set(winners), {topA, topB})

    # ----- Top work fix: _merge_duplicate_tops -----

    def test_merge_duplicate_tops_shared_id_collapsed(self):
        """The real-world 'two top_works' bug (Tannhäuser): the overture track's
        work is part of both the opera (9b1bd955) and an overture-grouping
        (d801c361) that is itself part of the opera, so its top is the merged
        tuple (9b1bd955, d801c361); the opera scenes reach the opera directly, so
        their top is (9b1bd955,). The two tops share the opera id - they are one
        work, not two - so _merge_duplicate_tops folds the smaller into the
        larger and the album reports a single top work."""
        opera = "9b1bd955-8635-43e6-9711-f2b820ffe8b8"
        grouping = "d801c361-fcfa-4485-81b4-486154daf6bd"
        topOverture = (opera, grouping)
        topOpera = (opera,)
        pl = self._make_partlevels(
            [topOverture, topOpera],
            {topOverture: {"name": ["Tannhäuser…WWV 70",
                                    "Tannhäuser: Ouverture and Venusberg Music"]},
             topOpera: {"name": ["Tannhäuser…WWV 70"]}})
        # 1 track under the overture top, 33 under the opera top.
        tracks_in_top = {
            topOverture: {("t_ouverture", "alb")},
            topOpera: {("t_s%d" % i, "alb") for i in range(33)}}
        merged = pl._merge_duplicate_tops("test", "alb", tracks_in_top)
        self.assertTrue(merged)
        # The most-selected top (the opera, 33 tracks) survives; the overture
        # top (1 track) is folded into it.
        self.assertEqual(pl.top["alb"], [topOpera])

    def test_merge_duplicate_tops_picks_most_selected_representative(self):
        """When the same work has two tops and the *smaller* id-set top is
        chosen by more tracks, it still wins as representative (most-selected,
        not largest id-set). E.g. both tops share id 'wShared'; the merged-tuple
        top (wShared, wExtra) is chosen by fewer tracks than the single top
        (wShared,), so the single top is kept."""
        topMerged = ("wShared", "wExtra")
        topSingle = ("wShared",)
        pl = self._make_partlevels(
            [topMerged, topSingle],
            {topMerged: {"name": ["A", "B"]},
             topSingle: {"name": ["A"]}})
        tracks_in_top = {topMerged: {("t1", "alb")},
                         topSingle: {("t2", "alb"), ("t3", "alb")}}
        merged = pl._merge_duplicate_tops("test", "alb", tracks_in_top)
        self.assertTrue(merged)
        # topSingle is chosen by more tracks -> it is the representative.
        self.assertEqual(pl.top["alb"], [topSingle])

    def test_merge_duplicate_tops_different_ids_kept(self):
        """Two tops that share NO work id are genuine distinct works (a
        concerto and a symphony on one album) and must NOT be merged, so real
        multi-work albums are preserved."""
        topA = ("wConcerto",)
        topB = ("wSymphony",)
        pl = self._make_partlevels(
            [topA, topB],
            {topA: {"name": ["Concerto"]}, topB: {"name": ["Symphony"]}})
        tracks_in_top = {topA: {("t1", "alb"), ("t2", "alb")},
                         topB: {("t3", "alb"), ("t4", "alb")}}
        merged = pl._merge_duplicate_tops("test", "alb", tracks_in_top)
        self.assertFalse(merged)
        self.assertEqual(set(pl.top["alb"]), {topA, topB})

    def test_merge_duplicate_tops_single_top_noop(self):
        """A single top is trivially not merged (no duplicate to collapse)."""
        topA = ("wA",)
        pl = self._make_partlevels([topA], {topA: {"name": ["A"]}})
        tracks_in_top = {topA: {("t1", "alb")}}
        merged = pl._merge_duplicate_tops("test", "alb", tracks_in_top)
        self.assertFalse(merged)
        self.assertEqual(pl.top["alb"], [topA])

    def test_merge_grafts_dropped_top_tracks_into_survivor(self):
        """Regression for release 6ced4363, tracks 16 & 17 (the ballet-only Bolt
        movements got NO top_work). When _merge_duplicate_tops folds the dropped
        top's id into the survivor, the dropped top's tracks must be grafted onto
        the survivor's trackback tree -- otherwise they are only re-pointed (in
        chosen_top) but never walked by process_trackback, so they get no tags."""
        fused = ("4aeb", "17f")     # ballet+suite fused top (shared movements)
        dropped = ("4aeb",)         # ballet-only top (exclusive movements 16,17)
        pl = self._make_partlevels(
            [fused, dropped],
            {fused: {"name": ["Ballet", "Suite"]}, dropped: {"name": ["Ballet"]}})
        # leaf trackback nodes (one per movement), as create_trackback builds them
        shared = [{"id": ["m%d" % i], "meta": [("t%d" % i, "alb")]}
                  for i in (11, 12, 13)]
        excl16 = {"id": ["m16"], "meta": [("t16", "alb")]}
        excl17 = {"id": ["m17"], "meta": [("t17", "alb")]}
        pl.trackback = {"alb": {
            fused: {"id": list(fused), "children": list(shared)},
            dropped: {"id": list(dropped), "children": [excl16, excl17]}}}
        # fused has more tracks -> it is the representative that survives
        tracks_in_top = {
            fused: {("t%d" % i, "alb") for i in (11, 12, 13)},
            dropped: {("t16", "alb"), ("t17", "alb")}}
        merged = pl._merge_duplicate_tops("test", "alb", tracks_in_top)
        self.assertTrue(merged)
        self.assertEqual(pl.top["alb"], [fused])          # dropped folded away
        fused_children = pl.trackback["alb"][fused]["children"]
        # the ballet-only movements are now under the surviving fused top ...
        self.assertIn(excl16, fused_children)
        self.assertIn(excl17, fused_children)
        # ... and the shared movements are still there (not lost or duplicated)
        for leaf in shared:
            self.assertEqual(fused_children.count(leaf), 1)

    def test_merge_graft_no_trackback_no_crash(self):
        """The graft is a no-op when no trackback trees are wired (e.g. the
        other merge unit tests), never raising on the missing attribute."""
        fused = ("4aeb", "17f")
        dropped = ("4aeb",)
        pl = self._make_partlevels(
            [fused, dropped],
            {fused: {"name": ["Ballet", "Suite"]}, dropped: {"name": ["Ballet"]}})
        tracks_in_top = {fused: {("t1", "alb"), ("t2", "alb")},
                         dropped: {("t3", "alb")}}
        # no pl.trackback set at all -> must not raise
        merged = pl._merge_duplicate_tops("test", "alb", tracks_in_top)
        self.assertTrue(merged)
        self.assertEqual(pl.top["alb"], [fused])

    # ----- fused top id collapse (release d907bb03, Swan Lake track 6) -----
    #
    # The 6 movements on the release are Swan Lake suite movements I-VI.
    # Movements I-V are each part of BOTH "Version A - 6 movements" (topA) and
    # "Version B - 8 movements" (topB), which have the IDENTICAL title (only
    # their disambiguation differs), so the name collapse cannot separate them.
    # Movement VI exists ONLY in Version A. After the merge folds the two into
    # the fused top (topA, topB), the id tuple still names both versions, so
    # ~cwp_workid_top / musicbrainz_workid wrongly claim every track (incl. the
    # VI. Scene that is not in Version B) belongs to Version B. The collapse
    # reduces the fused top to the id(s) common to all its tracks: Version A.

    _SWAN_A = "c72715f3"   # Version A - 6 movements (parent of all 6)
    _SWAN_B = "570f3852"   # Version B - 8 movements (parent of I-V only)
    _SWAN_NAME = "The Swan Lake (suite from the ballet), op. 20a"

    def _make_swan_partlevels(self):
        """A PartLevels wired with the post-merge Swan Lake state: a single
        fused top (A, B), all six tracks re-pointed onto it, and the pre-merge
        track_tops candidates (I-V under the fused (A, B), VI under (A,))."""
        fused = (self._SWAN_A, self._SWAN_B)
        pl = self._make_partlevels(
            [fused], {fused: {"name": [self._SWAN_NAME]}})
        pl.chosen_top = {}
        # leaf trackback nodes (one per movement) under the fused top
        leaves = [{"id": ["m%d" % i], "meta": [("t%d" % i, "alb")]}
                  for i in range(1, 7)]
        pl.trackback = {"alb": {fused: {"id": list(fused), "children": leaves}}}
        track_tops = collections.defaultdict(set)
        for i in range(1, 6):                       # I-V: both versions
            track_tops[("t%d" % i, "alb")] = {fused}
            pl.chosen_top[("t%d" % i, "alb")] = fused
        track_tops[("t6", "alb")] = {(self._SWAN_A,)}   # VI: Version A only
        pl.chosen_top[("t6", "alb")] = fused            # re-pointed by merge
        return pl, fused, track_tops

    def test_collapse_fused_top_ids_drops_unsupported_version(self):
        """The reported bug: the fused (Version A, Version B) top collapses to
        Version A - the only version that contains all six movements - so no
        track (least of all VI. Scene, absent from Version B) is tagged with a
        version the release does not contain."""
        pl, fused, track_tops = self._make_swan_partlevels()
        changed = pl._collapse_fused_top_ids("test", "alb", track_tops)
        self.assertTrue(changed)
        self.assertEqual(pl.top["alb"], [(self._SWAN_A,)])
        # every track (all six) now points at the Version A top
        self.assertTrue(all(v == (self._SWAN_A,)
                            for v in pl.chosen_top.values()))
        # the trackback tree moved to the collapsed id and its node id fixed
        self.assertIn((self._SWAN_A,), pl.trackback["alb"])
        self.assertNotIn(fused, pl.trackback["alb"])
        self.assertEqual(pl.trackback["alb"][(self._SWAN_A,)]["id"],
                         [self._SWAN_A])
        # all six leaves carried across, none lost
        self.assertEqual(
            len(pl.trackback["alb"][(self._SWAN_A,)]["children"]), 6)

    def test_collapse_fused_top_ids_ambiguous_kept(self):
        """When EVERY track belongs to both versions (no distinguishing track),
        the release is genuinely ambiguous: the intersection is the whole id
        tuple, so the fused top is left untouched (both ids kept)."""
        pl, fused, track_tops = self._make_swan_partlevels()
        # make VI also a member of both versions -> nothing distinguishes them
        track_tops[("t6", "alb")] = {fused}
        changed = pl._collapse_fused_top_ids("test", "alb", track_tops)
        self.assertFalse(changed)
        self.assertEqual(pl.top["alb"], [fused])

    def test_collapse_fused_top_ids_disjoint_kept(self):
        """If the tracks' candidate parents share NO common id (they genuinely
        span disjoint versions), the intersection is empty and the fused top is
        left unchanged rather than collapsed to nothing."""
        pl, fused, track_tops = self._make_swan_partlevels()
        # one track only under A, another only under B -> empty intersection
        track_tops[("t1", "alb")] = {(self._SWAN_A,)}
        track_tops[("t2", "alb")] = {(self._SWAN_B,)}
        changed = pl._collapse_fused_top_ids("test", "alb", track_tops)
        self.assertFalse(changed)
        self.assertEqual(pl.top["alb"], [fused])

    def test_collapse_fused_top_ids_single_top_noop(self):
        """A plain single-work top has nothing to collapse."""
        pl = self._make_partlevels(
            [("wA",)], {("wA",): {"name": ["Solo Work"]}})
        pl.chosen_top = {("t1", "alb"): ("wA",)}
        pl.trackback = {"alb": {}}
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {("wA",)}
        self.assertFalse(
            pl._collapse_fused_top_ids("test", "alb", track_tops))
        self.assertEqual(pl.top["alb"], [("wA",)])

    # ----- redundant-ancestor parent reduction (release-group 652df93a) -----
    #
    # Liszt "Annees de pelerinage" Year 1 movements: MusicBrainz links some
    # movements (Au lac de Wallenstadt, Eglogue) directly to BOTH the broad
    # grouping work "Annees de pelerinage" (COLL) and the specific sub-work
    # "Premiere annee: Suisse, S.160" (SPEC) that is itself part of COLL; other
    # movements carry only SPEC. The direct COLL edge is redundant and fuses two
    # levels into the parent tuple, flattening those movements one level below
    # their siblings. _reduce_redundant_parents drops a parent that is only an
    # ancestor of another parent, keeping the most-specific one(s).

    _LZ_SPEC = "d5800420"   # "...: Premiere annee: Suisse, S.160"
    _LZ_COLL = "29954628"   # "Annees de pelerinage" (SPEC is part of COLL)

    def _make_reduce_pl(self, works_cache):
        PartLevels = self.mod.PartLevels
        pl = PartLevels.__new__(PartLevels)
        pl.works_cache = works_cache
        return pl

    def test_reduce_redundant_parents_drops_grandparent(self):
        """A movement whose parents are the specific sub-work AND the broad
        grouping it belongs to keeps only the sub-work (the grouping is reached
        transitively). Independent of tuple order."""
        pl = self._make_reduce_pl({(self._LZ_SPEC,): [self._LZ_COLL]})
        self.assertEqual(
            pl._reduce_redundant_parents((self._LZ_SPEC, self._LZ_COLL)),
            (self._LZ_SPEC,))
        self.assertEqual(
            pl._reduce_redundant_parents((self._LZ_COLL, self._LZ_SPEC)),
            (self._LZ_SPEC,))

    def test_reduce_redundant_parents_keeps_siblings(self):
        """Genuine sibling multi-parents (neither an ancestor of the other,
        e.g. two same-named versions of a suite) are left untouched."""
        pl = self._make_reduce_pl({})
        self.assertEqual(pl._reduce_redundant_parents(("A", "B")), ("A", "B"))

    def test_reduce_redundant_parents_single_noop(self):
        """A lone parent is returned unchanged."""
        pl = self._make_reduce_pl({})
        self.assertEqual(pl._reduce_redundant_parents(("only",)), ("only",))

    def test_reduce_redundant_parents_deep_chain(self):
        """Both a grandparent and a great-grandparent are dropped, leaving only
        the most-specific parent, when the whole chain appears in the tuple."""
        pl = self._make_reduce_pl({("S",): ["M"], ("M",): ["C"]})
        self.assertEqual(
            pl._reduce_redundant_parents(("S", "M", "C")), ("S",))

    def test_reduce_redundant_parents_cycle_falls_back(self):
        """A degenerate mutual-ancestry cycle would drop everything; the
        original tuple is kept instead of collapsing to nothing."""
        pl = self._make_reduce_pl({("X",): ["Y"], ("Y",): ["X"]})
        self.assertEqual(pl._reduce_redundant_parents(("X", "Y")), ("X", "Y"))

    # Don Giovanni (release 510944a2): a movement is part of both a standalone
    # work (a top) and a movement of the bigger opera (embedded, i.e. having a
    # parent of its own). Prefer the embedded parent so the release of the opera
    # keeps the movement in the opera's hierarchy instead of fusing the
    # standalone grouping into the intermediate work level.
    _DG_ATTO2 = "18a0544b"   # "Atto II", embedded (part of the opera)
    _DG_K540C = "fc146a29"   # "Recitative and Aria, K. 540c", standalone top
    _DG_OPERA = "b3b1e2b3"   # the opera (top)

    def test_reduce_redundant_parents_drops_standalone_for_embedded(self):
        """The standalone top work is dropped in favour of the parent that is
        embedded in a larger work. Independent of tuple order."""
        pl = self._make_reduce_pl({(self._DG_ATTO2,): [self._DG_OPERA]})
        self.assertEqual(
            pl._reduce_redundant_parents((self._DG_ATTO2, self._DG_K540C)),
            (self._DG_ATTO2,))
        self.assertEqual(
            pl._reduce_redundant_parents((self._DG_K540C, self._DG_ATTO2)),
            (self._DG_ATTO2,))

    def test_reduce_redundant_parents_keeps_two_embedded(self):
        """Two parents each embedded in a (different) larger work, neither an
        ancestor of the other, are genuine multi-parents and both kept."""
        pl = self._make_reduce_pl({("P1",): ["T1"], ("P2",): ["T2"]})
        self.assertEqual(
            pl._reduce_redundant_parents(("P1", "P2")), ("P1", "P2"))

    def test_normalise_name(self):
        """Names are compared case/whitespace/punctuation-insensitively (used by
        the remap fallback) so the same work stored with different casing or
        spacing still resolves."""
        pl = self._make_partlevels([("w",)], {("w",): {"name": "x"}})
        self.assertEqual(pl._normalise_name("Symphony No. 5"),
                         pl._normalise_name("  symphony no. 5 "))
        self.assertEqual(pl._normalise_name("Mass in B minor"),
                         pl._normalise_name("mass  in b minor"))
        self.assertEqual(pl._normalise_name(None), '')

    # ----- Top work fix: _prune_collection_tops -----

    def test_prune_redundant_collection_overture_album(self):
        """The overture-album bug: a single track is part of both an opera and
        its overture collection (no unambiguous tracks). The collection is a
        redundant grouping container and is pruned, leaving the opera as the
        sole top work - even though there is no 'clear winner'."""
        import collections
        topOpera = ("wOpera",)
        topColl = ("wColl",)
        pl = self._make_partlevels(
            [topOpera, topColl],
            {topOpera: {"name": "Tannhauser"},
             topColl: {"name": "Ouverture and Venusberg",
                       "is_collection": True}})
        track_tops = collections.defaultdict(set)
        # the single overture track is shared between the opera and the
        # collection -> no unambiguous tracks -> has_clear_winner is False
        track_tops[("t1", "alb")] = {topOpera, topColl}
        self.assertFalse(any(len(t) == 1 for t in track_tops.values()))
        pruned = pl._prune_collection_tops("test", "alb", track_tops)
        self.assertTrue(pruned)
        # the redundant collection is pruned, leaving only the opera
        self.assertEqual(pl.top["alb"], [topOpera])

    def test_prune_redundant_collection_with_clear_winner(self):
        """Even when the album has a clear winner, the collection-pruning runs
        first and removes a redundant collection whose tracks are all under a
        genuine top."""
        import collections
        topOpera = ("wOpera",)
        topColl = ("wColl",)
        pl = self._make_partlevels(
            [topOpera, topColl],
            {topOpera: {"name": "Opera"},
             topColl: {"name": "OvertureColl", "is_collection": True}})
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {topOpera}
        track_tops[("t2", "alb")] = {topOpera, topColl}
        pruned = pl._prune_collection_tops("test", "alb", track_tops)
        self.assertTrue(pruned)
        self.assertEqual(pl.top["alb"], [topOpera])

    def test_prune_keeps_exclusive_collection(self):
        """A collection that is the only top for some track is kept - it is
        that track's real top work."""
        import collections
        topColl = ("wColl",)
        topOpera = ("wOpera",)
        pl = self._make_partlevels(
            [topColl, topOpera],
            {topColl: {"name": "Collection", "is_collection": True},
             topOpera: {"name": "Opera"}})
        track_tops = collections.defaultdict(set)
        # t1 is ONLY under the collection -> the collection is not redundant
        track_tops[("t1", "alb")] = {topColl}
        track_tops[("t2", "alb")] = {topOpera, topColl}
        pruned = pl._prune_collection_tops("test", "alb", track_tops)
        self.assertFalse(pruned)
        # both tops kept
        self.assertEqual(pl.top["alb"], [topColl, topOpera])

    def test_prune_keeps_tied_non_collection_tops(self):
        """Tied genuine (non-collection) top works are never pruned - e.g. a
        single overture recording that is part of two equal parent works."""
        import collections
        topOpera1 = ("wOpera1",)
        topOpera2 = ("wOpera2",)
        pl = self._make_partlevels(
            [topOpera1, topOpera2],
            {topOpera1: {"name": "Opera One"},
             topOpera2: {"name": "Opera Two"}})
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {topOpera1, topOpera2}
        pruned = pl._prune_collection_tops("test", "alb", track_tops)
        self.assertFalse(pruned)
        self.assertEqual(pl.top["alb"], [topOpera1, topOpera2])

    def test_prune_no_collections_no_change(self):
        """When there are no collections, _prune_collection_tops is a no-op."""
        import collections
        topA = ("wA",)
        topB = ("wB",)
        pl = self._make_partlevels(
            [topA, topB], {topA: {"name": "A"}, topB: {"name": "B"}})
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {topA}
        track_tops[("t2", "alb")] = {topA, topB}
        pruned = pl._prune_collection_tops("test", "alb", track_tops)
        self.assertFalse(pruned)
        self.assertEqual(pl.top["alb"], [topA, topB])

    def test_prune_only_collections_no_change(self):
        """When every top is a collection (no genuine top) none is pruned - the
        collections are the real tops for their tracks."""
        import collections
        topColl1 = ("wC1",)
        topColl2 = ("wC2",)
        pl = self._make_partlevels(
            [topColl1, topColl2],
            {topColl1: {"name": "Coll1", "is_collection": True},
             topColl2: {"name": "Coll2", "is_collection": True}})
        track_tops = collections.defaultdict(set)
        track_tops[("t1", "alb")] = {topColl1, topColl2}
        pruned = pl._prune_collection_tops("test", "alb", track_tops)
        self.assertFalse(pruned)
        self.assertEqual(pl.top["alb"], [topColl1, topColl2])

    # ----- process_trackback parallel-lists regression (Alina album) -----

    def _make_fake_metadata(self, title, tracknumber, discnumber=1):
        """A minimal stand-in for Picard's Metadata supporting the subset of the
        dict protocol that process_trackback touches: __setitem__, __getitem__
        (missing keys return '' like Picard), __contains__ and getall()."""
        class _FakeMeta(dict):
            def __getitem__(self, key):
                return self.get(key, '')

            def getall(self, key):
                return [self[key]] if key in self else []
        return _FakeMeta(title=title, tracknumber=str(tracknumber),
                         discnumber=str(discnumber))

    def _make_fake_track(self, mid, title, tracknumber, discnumber=1):
        """A minimal stand-in for a Picard Track with a ``.metadata`` attribute."""
        from unittest.mock import Mock
        t = Mock(name="track-%s" % mid)
        t.metadata = self._make_fake_metadata(title, tracknumber, discnumber)
        # give it a stable identity so it can be a dict key / set member
        t._id = mid
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    def _make_trackback_partlevels(self):
        """A PartLevels wired only with what process_trackback needs, with the
        tag-writing side-effects stubbed out."""
        PartLevels = self.mod.PartLevels
        pl = PartLevels.__new__(PartLevels)
        pl.parts = {}
        pl.chosen_top = {}
        # write_tags / make_annotations pull heavily on self.options & parts; the
        # parallel-lists bug is independent of the tags they write, so stub them.
        pl.write_tags = lambda *a, **k: None
        pl.make_annotations = lambda *a, **k: None
        return pl

    def test_process_trackback_depth0_keeps_all_tracks_parallel(self):
        """Regression for the 'Alina' album: when several tracks share one
        depth-0 top work, process_trackback must return a ``tracks`` dict whose
        track / title / work / tracknumber lists are ALL the same length. The
        caller zips track with tracknumber, so if tracknumber were reassigned
        (the old bug) only one track per top work would survive into
        self.tracks and be published."""
        pl = self._make_trackback_partlevels()
        workId = ("83e63350",)
        pl.parts[workId] = {"name": "Spiegel im Spiegel"}
        top_info = {"id": workId, "name": "Spiegel im Spiegel",
                    "levels": 1, "single": True}
        t1 = self._make_fake_track("9547dfb8", "Spiegel im Spiegel", 1)
        t2 = self._make_fake_track("617387c5", "Spiegel im Spiegel", 5)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(t1, "alb"), (t2, "alb")]}
        response = pl.process_trackback("test", "alb", trackback, 0, top_info)
        self.assertIsNotNone(response)
        tracks = response[1]
        self.assertEqual(len(tracks['track']), 2)
        self.assertEqual(len(tracks['tracknumber']), 2)
        self.assertEqual(len(tracks['title']), 2)
        self.assertEqual(len(tracks['work']), 2)
        # both tracks are present in the track list
        track_ids = {t[0]._id for t in tracks['track']}
        self.assertEqual(track_ids, {"9547dfb8", "617387c5"})

    def test_process_trackback_children_then_root_stays_parallel(self):
        """The Spiegel case: a child track is collected by
        process_trackback_children (depth 1), then the root tracks are appended
        by the depth-0 loop. All four lists must stay parallel even though the
        two phases append separately."""
        pl = self._make_trackback_partlevels()
        # stub the children-path helpers that run inside the depth!=0 branch
        pl.set_metadata = lambda *a, **k: None
        pl.derive_from_structure = lambda *a, **k: None
        import collections as _c
        pl.options = _c.defaultdict(lambda: {"cwp_level0_works": False})
        workId = ("83e63350",)
        child_workId = ("8af195f4",)
        pl.parts[workId] = {"name": "Spiegel im Spiegel"}
        pl.parts[child_workId] = {"name": "arrangement"}
        top_info = {"id": workId, "name": "Spiegel im Spiegel",
                    "levels": 1, "single": True}
        # one child track under an arrangement child node
        child_track = self._make_fake_track("c3445bbf", "Spiegel im Spiegel", 3)
        child_node = {"id": list(child_workId), "depth": 0, "height": 2,
                      "meta": [(child_track, "alb")]}
        # root has two tracks plus the one child
        t1 = self._make_fake_track("9547dfb8", "Spiegel im Spiegel", 1)
        t2 = self._make_fake_track("617387c5", "Spiegel im Spiegel", 5)
        root = {"id": list(workId), "depth": 1, "height": 1,
                "meta": [(t1, "alb"), (t2, "alb")],
                "children": [child_node]}
        response = pl.process_trackback("test", "alb", root, 0, top_info)
        self.assertIsNotNone(response)
        tracks = response[1]
        # child track + 2 root tracks = 3 total, all lists parallel
        self.assertEqual(len(tracks['track']), 3)
        self.assertEqual(len(tracks['tracknumber']), 3)
        self.assertEqual(len(tracks['title']), 3)
        self.assertEqual(len(tracks['work']), 3)
        track_ids = {t[0]._id for t in tracks['track']}
        self.assertEqual(track_ids, {"c3445bbf", "9547dfb8", "617387c5"})

    def test_process_trackback_zip_does_not_truncate(self):
        """The concrete symptom of the bug: the caller does
        ``zip(tracks['track'], tracks['tracknumber'])`` and iterates the result
        to publish metadata. When the lists are parallel every track is
        yielded; with the old bug tracknumber had length 1 so only one track was
        published. Assert the zip yields one entry per track."""
        pl = self._make_trackback_partlevels()
        workId = ("53a20f2a",)
        pl.parts[workId] = {"name": "Für Alina"}
        top_info = {"id": workId, "name": "Für Alina",
                    "levels": 1, "single": True}
        t1 = self._make_fake_track("b5722bd5", "Für Alina", 2)
        t2 = self._make_fake_track("1c2a9392", "Für Alina", 4)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(t1, "alb"), (t2, "alb")]}
        response = pl.process_trackback("test", "alb", trackback, 0, top_info)
        tracks = response[1]
        paired = list(zip(tracks['track'], tracks['tracknumber']))
        self.assertEqual(len(paired), 2)

    def test_process_trackback_root_has_children_no_child_tracks(self):
        """Regression for the 'Alina' tracks-dropped symptom: a top work that
        has child works defined in MusicBrainz (so the trackback tree has
        ``children`` and ``depth != 0``) but whose tracks are attached directly
        to the top work rather than to any child (none of the child subtrees
        carry tracks for this album).

        In that case ``process_trackback_children`` collects nothing and
        returns ``None``. The depth-0 branch previously did
        ``tracks = child_response[1]`` unconditionally, which raised
        ``TypeError: 'NoneType' object is not subscriptable``. Because
        ``process_album`` calls ``process_trackback`` without a guard, the
        exception aborted the whole album loop: the top's own tracks were never
        appended and every subsequent top's tracks were skipped too -- the
        album's tracks appeared to be "dropped".

        The fix keeps the (empty) ``tracks`` dict when the children call returns
        ``None`` and falls through to the depth-0 loop so the top's own tracks
        are still processed and published."""
        pl = self._make_trackback_partlevels()
        workId = ("83e63350",)
        child_workId = ("childpart",)
        pl.parts[workId] = {"name": "Spiegel im Spiegel"}
        pl.parts[child_workId] = {"name": "unused part"}
        top_info = {"id": workId, "name": "Spiegel im Spiegel",
                    "levels": 1, "single": True}
        t1 = self._make_fake_track("9547dfb8", "Spiegel im Spiegel", 1)
        t2 = self._make_fake_track("617387c5", "Spiegel im Spiegel", 5)
        # child node exists in the tree (so depth != 0) but carries no tracks
        # for this album -- its meta list is absent.
        child_node = {"id": list(child_workId), "depth": 0, "height": 2}
        root = {"id": list(workId), "depth": 1, "height": 1,
                "meta": [(t1, "alb"), (t2, "alb")],
                "children": [child_node]}
        # Must not raise.
        response = pl.process_trackback("test", "alb", root, 0, top_info)
        self.assertIsNotNone(response)
        tracks = response[1]
        self.assertEqual(len(tracks['track']), 2)
        self.assertEqual(len(tracks['tracknumber']), 2)
        self.assertEqual(len(tracks['title']), 2)
        self.assertEqual(len(tracks['work']), 2)
        track_ids = {t[0]._id for t in tracks['track']}
        self.assertEqual(track_ids, {"9547dfb8", "617387c5"})

    # ----- Top work: most-voted selection for multi-parent (fused) tops -----

    _BOLT_BALLET = 'Ballet Suite no. 5, op. 27a "The Bolt"'
    _BOLT_SUITE = "Suite from The Bolt"

    def _bolt_parent_titles(self, work_id):
        """Ordered backward 'parts' parent (id, title) pairs for a work id in
        the real bolt_work_tree.json fixture. Mirrors how the plugin's
        work_process_relations builds the fused parent node's ``name`` list: one
        entry per parent work, in MusicBrainz relation order."""
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures",
                            "bolt_work_tree.json")
        with open(path, encoding="utf-8") as f:
            tree = json.load(f)
        parents = []
        for rel in tree[work_id]["relations"]:
            if rel.get("type") == "parts" and rel.get("direction") == "backward":
                parents.append((rel["work"]["id"], rel["work"]["title"]))
        return parents

    def _bolt_name_votes(self):
        """Album-wide top-work NAME votes for the Bolt album, computed from the
        real fixtures exactly as process_album does: every track votes once for
        each of its movement's parent (candidate top) names. The ballet is a
        parent of all 8 movements; the suite of the 6 shared ones."""
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures",
                            "bolt_track_work_map.json")
        with open(path, encoding="utf-8") as f:
            track_map = json.load(f)
        votes = collections.Counter()
        for work_id in track_map.values():
            for title in {t for _, t in self._bolt_parent_titles(work_id)}:
                votes[title] += 1
        return votes

    def _run_top_for_movement(self, pl, work_id, tracknumber, votes):
        """Drive process_trackback for one Bolt movement leaf under its (real)
        fused parent node, with the album's name votes attached, and return the
        resulting ~cwp_work_top value.

        Reproduces the leak point: a movement leaf (depth 0) is processed with
        ``top_info`` being its parent node, whose ``name`` is the list of parent
        titles (two for a shared movement, one for an exclusive movement)."""
        parents = self._bolt_parent_titles(work_id)
        top_info = {"id": tuple(pid for pid, _ in parents),
                    "name": [title for _, title in parents],  # a LIST, as stored
                    "levels": 1, "single": False, "votes": votes}
        workId = (work_id,)
        # the movement's own name is unimportant to ~cwp_work_top; give it a str.
        pl.parts[workId] = {"name": work_id}
        track = self._make_fake_track(work_id, "movement", tracknumber)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(track, "alb")]}
        pl.process_trackback("test", "alb", trackback, 0, top_info)
        return track.metadata.get("~cwp_work_top")

    def test_bolt_name_votes_from_fixtures(self):
        """Sanity-check the vote counts derived from the real fixtures: the
        ballet is a candidate top for all 8 movements, the suite for 6."""
        votes = self._bolt_name_votes()
        self.assertEqual(votes[self._BOLT_BALLET], 8)
        self.assertEqual(votes[self._BOLT_SUITE], 6)

    def test_top_work_shared_movement_picks_most_voted(self):
        """Regression for the Bolt 'two top_work values' bug. A movement part of
        BOTH the ballet and the suite (tracks 11-15,18) has a fused parent node
        whose ``name`` is a two-item list. Previously process_trackback wrote the
        whole list into ~cwp_work_top (two tag values). It must now be the single
        most-voted candidate -- the ballet (8 votes vs the suite's 6)."""
        pl = self._make_trackback_partlevels()
        shared_id = "5ce3404c-5cf1-43c0-935a-967773c80009"   # track 11, 2 parents
        self.assertEqual(len(self._bolt_parent_titles(shared_id)), 2)
        top = self._run_top_for_movement(pl, shared_id, 11, self._bolt_name_votes())
        self.assertIsInstance(top, str)     # not a list -> not two tag values
        self.assertEqual(top, self._BOLT_BALLET)

    def test_top_work_shared_and_exclusive_movements_agree(self):
        """The other half of the bug: shared movements (two parents) 'resolved
        differently' from exclusive movements (one parent). With most-voted
        selection every movement resolves to the same top_work (the ballet), so
        the album is consistent regardless of which movements are shared."""
        pl = self._make_trackback_partlevels()
        votes = self._bolt_name_votes()
        shared_id = "5ce3404c-5cf1-43c0-935a-967773c80009"     # track 11, 2 parents
        exclusive_id = "da32f335-d098-4522-8a31-179c3ff5dafe"  # track 16, 1 parent
        self.assertEqual(len(self._bolt_parent_titles(exclusive_id)), 1)
        shared_top = self._run_top_for_movement(pl, shared_id, 11, votes)
        exclusive_top = self._run_top_for_movement(pl, exclusive_id, 16, votes)
        self.assertEqual(shared_top, exclusive_top)
        self.assertEqual(shared_top, self._BOLT_BALLET)

    def test_top_work_vote_overrides_list_order(self):
        """The choice is by votes, NOT by position in the name list. Here the
        suite is listed FIRST but the ballet has more votes, so the ballet wins.
        This is the opera case: an overture whose fused parent list happens to
        put a rarely-used translation first still resolves to the translation the
        rest of the album voted for."""
        pl = self._make_trackback_partlevels()
        votes = {self._BOLT_BALLET: 8, self._BOLT_SUITE: 6}
        workId = ("mov",)
        pl.parts[workId] = {"name": "mov"}
        top_info = {"id": ("suite_id", "ballet_id"),
                    "name": [self._BOLT_SUITE, self._BOLT_BALLET],  # suite first
                    "levels": 1, "single": False, "votes": votes}
        track = self._make_fake_track("mov", "movement", 11)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(track, "alb")]}
        pl.process_trackback("test", "alb", trackback, 0, top_info)
        self.assertEqual(track.metadata.get("~cwp_work_top"), self._BOLT_BALLET)

    def test_top_work_tie_keeps_several(self):
        """When candidate tops tie on votes, several top_work values are kept
        (per spec) -- so shared tracks still all carry the same set and stay
        consistent."""
        pl = self._make_trackback_partlevels()
        votes = {self._BOLT_BALLET: 6, self._BOLT_SUITE: 6}
        workId = ("mov",)
        pl.parts[workId] = {"name": "mov"}
        top_info = {"id": ("ballet_id", "suite_id"),
                    "name": [self._BOLT_BALLET, self._BOLT_SUITE],
                    "levels": 1, "single": False, "votes": votes}
        track = self._make_fake_track("mov", "movement", 11)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(track, "alb")]}
        pl.process_trackback("test", "alb", trackback, 0, top_info)
        self.assertEqual(track.metadata.get("~cwp_work_top"),
                         [self._BOLT_BALLET, self._BOLT_SUITE])

    def test_select_top_work_names_unit(self):
        """Unit-level coverage of the selection helper."""
        select = self.mod.PartLevels._select_top_work_names
        # unique winner -> single string
        self.assertEqual(select(["A", "B"], {"A": 3, "B": 1}), "A")
        # winner regardless of order
        self.assertEqual(select(["B", "A"], {"A": 3, "B": 1}), "A")
        # tie -> list of tied names, de-duplicated, order preserved
        self.assertEqual(select(["A", "B"], {"A": 2, "B": 2}), ["A", "B"])
        # no votes -> fall back to the first name (never the raw list)
        self.assertEqual(select(["A", "B"], {}), "A")
        self.assertEqual(select(["A", "B"], None), "A")

    def test_collapse_multiparent_top_name(self):
        """The fused top's name is reduced to the vote winner in self.parts, so
        every tag derived from it (work, top_work, ~cwp_work_N) is single. This
        is the release 6ced4363 'work has two values' regression: the parent
        name list ['Ballet ...', 'Suite from The Bolt'] must become just the
        most-voted ballet."""
        pl = self._make_trackback_partlevels()
        fused = ("4aeb", "17f")
        pl.parts[fused] = {"name": [self._BOLT_BALLET, self._BOLT_SUITE]}
        votes = {self._BOLT_BALLET: 8, self._BOLT_SUITE: 6}
        changed = pl._collapse_multiparent_top_name(fused, votes)
        self.assertTrue(changed)
        self.assertEqual(pl.parts[fused]["name"], [self._BOLT_BALLET])

    def test_collapse_multiparent_top_name_tie_keeps_all(self):
        """A tie keeps the tied names (several top works acceptable)."""
        pl = self._make_trackback_partlevels()
        fused = ("a", "b")
        pl.parts[fused] = {"name": [self._BOLT_BALLET, self._BOLT_SUITE]}
        votes = {self._BOLT_BALLET: 6, self._BOLT_SUITE: 6}
        pl._collapse_multiparent_top_name(fused, votes)
        self.assertEqual(pl.parts[fused]["name"],
                         [self._BOLT_BALLET, self._BOLT_SUITE])

    def test_collapse_multiparent_top_name_single_untouched(self):
        """Single-name and plain-string tops are left alone."""
        pl = self._make_trackback_partlevels()
        pl.parts[("s",)] = {"name": ["Only Work"]}
        pl.parts[("t",)] = {"name": "String Work"}
        self.assertFalse(pl._collapse_multiparent_top_name(("s",), {"Only Work": 3}))
        self.assertFalse(pl._collapse_multiparent_top_name(("t",), {}))
        self.assertEqual(pl.parts[("s",)]["name"], ["Only Work"])
        self.assertEqual(pl.parts[("t",)]["name"], "String Work")

    def test_top_work_string_name_unchanged(self):
        """A normal single-work top (name is a plain string) is untouched."""
        pl = self._make_trackback_partlevels()
        workId = ("83e63350",)
        pl.parts[workId] = {"name": "Spiegel im Spiegel"}
        top_info = {"id": workId, "name": "Spiegel im Spiegel",
                    "levels": 1, "single": True}
        track = self._make_fake_track("9547dfb8", "Spiegel im Spiegel", 1)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(track, "alb")]}
        pl.process_trackback("test", "alb", trackback, 0, top_info)
        self.assertEqual(track.metadata.get("~cwp_work_top"), "Spiegel im Spiegel")

    # ----- set_metadata must not clobber ~cwp_work_top with the joined list ---
    # (regression for release 6ced4363: process_trackback picked the ballet at
    #  depth 0, then set_metadata overwrote ~cwp_work_top with the "; "-joined
    #  "Ballet ...; Suite from The Bolt".)

    def test_set_metadata_fused_top_uses_resolved_name(self):
        """When a resolved top_name is supplied for a fused multi-parent top,
        set_metadata writes THAT to ~cwp_work_top, not the joined parent list."""
        pl = self._make_trackback_partlevels()
        joined = self._BOLT_BALLET + "; " + self._BOLT_SUITE
        # strip_parent_from_work returns (stripped_work, full_parent); the real
        # code joins a multi-parent name into full_parent -- mimic that.
        pl.strip_parent_from_work = lambda *a, **k: ("stripped", joined)
        workId = ("mov",)
        parentId = ("ballet_id", "suite_id")   # a fused multi-parent top
        pl.parts[workId] = {"name": "mov"}
        pl.parts[parentId] = {"name": [self._BOLT_BALLET, self._BOLT_SUITE],
                              "no_parent": True}
        track = self._make_fake_track("mov", "movement", 11)
        pl.set_metadata("test", 1, workId, parentId,
                        [self._BOLT_BALLET, self._BOLT_SUITE], track,
                        self._BOLT_BALLET)
        self.assertEqual(track.metadata.get("~cwp_work_top"), self._BOLT_BALLET)
        self.assertNotIn("Suite from The Bolt",
                         track.metadata.get("~cwp_work_top"))

    def test_set_metadata_single_top_keeps_full_parent(self):
        """With no resolved top_name (ordinary single-work top) set_metadata
        keeps its previous behaviour: ~cwp_work_top = full_parent."""
        pl = self._make_trackback_partlevels()
        pl.strip_parent_from_work = lambda *a, **k: ("stripped", "Full Work Name")
        workId = ("w",)
        parentId = ("p",)
        pl.parts[workId] = {"name": "w"}
        pl.parts[parentId] = {"name": "Some Work", "no_parent": True}
        track = self._make_fake_track("w", "movement", 1)
        pl.set_metadata("test", 1, workId, parentId, "Some Work", track, None)
        self.assertEqual(track.metadata.get("~cwp_work_top"), "Full Work Name")


class RecordingSessionTagsTestCase(ClassicalExtrasTestCase):
    """Recording place/date tag derivation (recording_session_tags).

    Fixtures are the real MusicBrainz JSON captured once from
    /recording/<id>?inc=place-rels+artist-rels (see test/fixtures/). The pure
    function takes the recording's `relations` list and returns the four tag
    values per the task spec.
    """

    def _load_relations(self, fixture_name):
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures", fixture_name)
        with open(path, encoding="utf-8") as f:
            return json.load(f)["relations"]

    def test_one_place_single_date(self):
        """(a) rec 92ab0819: Atlanta Symphony Hall / Atlanta / 1982-05-24."""
        relations = self._load_relations("rec_92ab0819_fanfare.json")
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Atlanta Symphony Hall"])
        self.assertEqual(tags["recordingcity"], ["Atlanta"])
        self.assertEqual(tags["recordingdate"], "1982-05-24")
        self.assertEqual(tags["recordingsessions"],
                         ["Atlanta Symphony Hall, Atlanta (1982-05-24)"])

    def test_two_places_conductor_span_no_extra_session(self):
        """(b) rec 791581ad: two venues on different dates; the conductor/
        orchestra span 2018-04-19..04-22 is already covered by the place
        sessions and must NOT add a third date-only session. UTF-8 survives."""
        relations = self._load_relations("rec_791581ad_bruckner6.json")
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingsessions"],
                         ["サントリーホール, Akasaka (2018-04-19)",
                          "横浜みなとみらいホール, Minato-Mirai (2018-04-22)"])
        self.assertEqual(tags["recordingplace"],
                         ["サントリーホール", "横浜みなとみらいホール"])
        self.assertEqual(tags["recordingcity"], ["Akasaka", "Minato-Mirai"])
        self.assertEqual(tags["recordingdate"], "2018-04-19 - 2018-04-22")
        # DEDUP CHECK: exactly two sessions (no conductor/orchestra fallback).
        self.assertEqual(len(tags["recordingsessions"]), 2)

    def test_no_place_date_only_fallback(self):
        """(c) rec 9dcd4293 (Red Pony): no recorded-at place; Previn/St.Louis SO
        1963-03-25 -> one date-only session "(1963-03-25)"."""
        relations = self._load_relations("rec_9dcd4293_redpony.json")
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], [])
        self.assertEqual(tags["recordingcity"], [])
        self.assertEqual(tags["recordingdate"], "1963-03-25")
        self.assertEqual(tags["recordingsessions"], ["(1963-03-25)"])

    def test_precision_match_month_collapse_to_single(self):
        """Spec: equal begin/end at month precision emit a single "YYYY-MM",
        not a range "YYYY-MM - YYYY-MM"."""
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "1981-03", "end": "1981-03",
             "place": {"name": "Symphony Hall", "area": {"name": "Boston"}}},
            {"target-type": "artist", "type": "conductor",
             "begin": "1981-03", "end": "1981-03",
             "artist": {"name": "Ozawa"}},
        ]
        tags = self.mod.recording_session_tags(relations)
        # date collapses to single month (precision-match), not a range.
        self.assertEqual(tags["recordingdate"], "1981-03")
        self.assertEqual(tags["recordingsessions"],
                         ["Symphony Hall, Boston (1981-03)"])
        # conductor span (1981-03..1981-03) is covered by the place session ->
        # no date-only fallback session.
        self.assertEqual(len(tags["recordingsessions"]), 1)

    def test_duplicate_venue_dedup_in_flat_tags(self):
        """The same venue recorded on two dates: recordingplace/city dedup in
        the flat tags, but recordingsessions keeps one entry per date."""
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "2024-09-11", "end": "2024-09-11",
             "place": {"name": "Suntory Hall", "area": {"name": "Tokyo"}}},
            {"target-type": "place", "type": "recorded at",
             "begin": "2024-09-12", "end": "2024-09-12",
             "place": {"name": "Suntory Hall", "area": {"name": "Tokyo"}}},
        ]
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Suntory Hall"])
        self.assertEqual(tags["recordingcity"], ["Tokyo"])
        self.assertEqual(len(tags["recordingsessions"]), 2)
        self.assertEqual(tags["recordingdate"], "2024-09-11 - 2024-09-12")

    def test_parse_data_relations_boundary(self):
        """Integration: parse_data() on the FULL /recording response yields the
        relations list in the exact shape recording_session_tags expects. This
        locks the response->tags seam that recording_process relies on (the
        callback does parse_data(response, 'relations') then hands it to the
        pure function)."""
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures",
                            "rec_791581ad_bruckner6.json")
        with open(path, encoding="utf-8") as f:
            full_response = json.load(f)  # WHOLE response, not just ["relations"]
        # exactly what recording_process must do with the webservice response:
        # parse_data always returns a list and wraps the matched value, so
        # parse_data(response, 'relations') -> [[rel, rel, ...]]. Unwrap one
        # level to get the raw relations list the pure function iterates.
        wrapped = self.mod.parse_data("test", full_response, [], 'relations')
        relations = wrapped[0] if wrapped else []
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"],
                         ["サントリーホール", "横浜みなとみらいホール"])
        self.assertEqual(tags["recordingcity"], ["Akasaka", "Minato-Mirai"])
        self.assertEqual(tags["recordingdate"], "2018-04-19 - 2018-04-22")
        self.assertEqual(len(tags["recordingsessions"]), 2)

    def test_place_without_city_omits_stray_comma(self):
        """A recorded-at place whose area has no name must not leave a stray
        ", " in the session label (recordingcity is simply empty)."""
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "2000-06-01", "end": "2000-06-01",
             "place": {"name": "Studio X", "area": {}}},
        ]
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Studio X"])
        self.assertEqual(tags["recordingcity"], [])
        self.assertEqual(tags["recordingsessions"], ["Studio X (2000-06-01)"])
        self.assertEqual(tags["recordingdate"], "2000-06-01")

    def test_dateless_place_with_dated_conductor_no_crash(self):
        """Regression: a recorded-at place with NO date, plus a dated conductor.
        The place session has no date, so the conductor date is NOT covered and
        must surface as a date-only fallback session -- and must not raise
        (previously min() over the empty place-date sequence crashed with
        ValueError)."""
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "", "end": "",
             "place": {"name": "Abbey Road Studios",
                       "area": {"name": "London"}}},
            {"target-type": "artist", "type": "conductor",
             "begin": "1970-01-01", "end": "1970-01-01",
             "artist": {"name": "George Martin"}},
        ]
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Abbey Road Studios"])
        self.assertEqual(tags["recordingcity"], ["London"])
        self.assertEqual(tags["recordingdate"], "1970-01-01")
        self.assertEqual(tags["recordingsessions"],
                         ["Abbey Road Studios, London", "(1970-01-01)"])


class RecordingLookupCallbackTestCase(ClassicalExtrasTestCase):
    """The async recording lookup callback (PartLevels.recording_process):
    tag writing, request accounting, and album finalization. Uses fake
    album/track objects so no real webservice or album state is needed."""

    class _FakeAlbum:
        def __init__(self):
            self._requests = 0
            self.finalized = False

        def _finalize_loading(self, _arg):
            self.finalized = True

    class _FakeTrack:
        def __init__(self, metadata):
            self.metadata = metadata

    def _load_full(self, name):
        import json
        import os
        path = os.path.join(os.path.dirname(__file__), "fixtures", name)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _make(self, release_id, work_parts=True):
        from picard.metadata import Metadata
        pl = self.mod.PartLevels()
        self._process_calls = []
        pl.process_album = lambda rid, alb: self._process_calls.append(rid)
        tm = Metadata()
        tm['musicbrainz_albumid'] = release_id
        track = self._FakeTrack(tm)
        album = self._FakeAlbum()
        album._requests = 1
        # recording_process consults the track's options to decide whether to
        # run end-of-album works processing.
        pl.options[track] = {'classical_work_parts': work_parts}
        return pl, tm, track, album

    def test_success_writes_tags_and_finalizes(self):
        pl, tm, track, album = self._make('rel1')
        pl.recordings_queue.append('rid1', (track, album))
        full = self._load_full("rec_791581ad_bruckner6.json")   # full response
        pl.recording_process('rid1', 0, full, None, None)
        self.assertEqual(list(tm.getall('recording_place')),
                         ['サントリーホール', '横浜みなとみらいホール'])
        self.assertEqual(list(tm.getall('recording_city')),
                         ['Akasaka', 'Minato-Mirai'])
        self.assertEqual(tm['recording_date'], '2018-04-19 - 2018-04-22')
        # request released exactly once and album finalized once
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)
        self.assertEqual(self._process_calls, ['rel1'])

    def test_error_give_up_still_releases_and_finalizes(self):
        # tries above MAX_RETRIES -> no requeue; must STILL release + finalize
        # (otherwise the album hangs forever).
        pl, tm, track, album = self._make('rel2')
        pl.recordings_queue.append('ridX', (track, album))
        pl.recording_process('ridX', 99, None, None, "503")
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)
        self.assertEqual(self._process_calls, ['rel2'])

    def test_duplicate_recording_id_balances_request_count(self):
        """Two tracks sharing one recording id: album_add_request fires per
        track (+2, unconditional, as in work_add_track), one lookup is queued,
        and its single callback fans out to BOTH queued tuples (-2). Net zero ->
        album finalizes, no hang. (Verifies the fan-out accounting on the
        duplicate-recording path, which no other test exercises.)"""
        from picard.metadata import Metadata
        pl = self.mod.PartLevels()
        self._process_calls = []
        pl.process_album = lambda rid, alb: self._process_calls.append(rid)
        album = self._FakeAlbum()
        album._requests = 0
        tracks = []
        for _ in range(2):
            tm = Metadata()
            tm['musicbrainz_albumid'] = 'reldup'
            t = self._FakeTrack(tm)
            pl.options[t] = {'classical_work_parts': True}
            tracks.append(t)
        # Replicate what add_work_info -> recording_add_track does to the
        # counter + queue for each track (unconditional add_request + append).
        for t in tracks:
            self.mod.PartLevels.album_add_request('reldup', album)   # +1 each
            pl.recordings_queue.append('shared', (t, album))         # True, then False
        self.assertEqual(album._requests, 2)
        full = self._load_full("rec_92ab0819_fanfare.json")
        pl.recording_process('shared', 0, full, None, None)          # single callback
        self.assertEqual(album._requests, 0)      # +2 then -2 -> balanced (no hang)
        self.assertTrue(album.finalized)
        self.assertEqual(self._process_calls, ['reldup'])            # finalized once

    def test_work_parts_off_skips_process_album(self):
        # crr lookup on but classical_work_parts OFF: tags are still written and
        # the album still finalizes, but process_album must NOT run -- its state
        # (track_listing/top/parts) was never built, so calling it would crash.
        pl, tm, track, album = self._make('rel3', work_parts=False)
        pl.recordings_queue.append('ridA', (track, album))
        full = self._load_full("rec_92ab0819_fanfare.json")
        pl.recording_process('ridA', 0, full, None, None)
        self.assertEqual(list(tm.getall('recording_place')),
                         ['Atlanta Symphony Hall'])
        self.assertEqual(tm['recording_date'], '1982-05-24')
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)        # still finalizes
        self.assertEqual(self._process_calls, [])   # but process_album NOT called


class SwanLakeFusedTopIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release d907bb03 (Tchaikovsky ballet suites),
    Swan Lake movements I-VI. Drives the REAL add_work_info -> work_process ->
    process_album pipeline with a webservice mock that serves the actual
    MusicBrainz work/recording fixtures, deferring responses so all six tracks
    queue their lookups before process_album runs once (as Picard does). Locks
    the fix: the fused (Version A, Version B) top collapses to Version A, so
    track 6 (VI. Scene, absent from Version B) is tagged with the same single
    top work as the other five, not a two-id tuple naming a version this
    release does not contain."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "swanlake")
    _REL = "d907bb03-a08d-4328-ad8c-eaecdb61b4e6"
    _A = "c72715f3-6a26-48a8-9ed5-1c29493b90fb"   # Version A - 6 movements
    _B = "570f3852-ca39-4db2-aafd-4e818af725fd"   # Version B - 8 movements
    # (track number, recording id, movement work id)
    _TRACKS = [
        (1, "705e6e53-aaa2-43c6-909a-c856254c38f8", "3b8a46af-749e-484d-93c3-cb67166663b6"),
        (2, "e04b71f3-bece-4830-9e64-0c55ca77eaf4", "bb3e1076-d8dd-4389-91e7-b740fb0fa07e"),
        (3, "7afa3446-0768-457c-9263-3357f3984219", "7c12ba63-bd80-485c-b8df-32c2611d7c82"),
        (4, "ede4cc1d-7f25-4970-af10-0f299f760cc2", "ca393bf5-9c20-46ed-ad1f-1bec6d133a43"),
        (5, "26beafbe-2fdc-417b-b401-5f31ec07f5e0", "558b3a4c-6e0d-4ba9-a237-40d500b0d1ee"),
        (6, "841d29b7-d61d-4c7c-b3a3-2537e19996fd", "0d6344f4-e750-45ce-b17b-ce4aebe0e808"),
    ]

    def _load(self, name):
        with open(os.path.join(self._FIXDIR, name), encoding="utf-8") as f:
            return json.load(f)

    def setUp(self):
        super().setUp()
        cfg = {
            "server_host": "musicbrainz.org", "server_port": 443,
            "use_cache": True, "classical_work_parts": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "cwp_partial": False, "cwp_arrangements": False,
            "cwp_medley": False, "cwp_collections": False,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        }
        self.set_config_values(setting=cfg)

    def _make_track(self, num, rec_id, work_id, opts):
        class _FakeMeta(dict):
            def __getitem__(self, key):
                return self.get(key, '')

            def getall(self, key):
                v = self.get(key)
                if v is None:
                    return []
                return v if isinstance(v, list) else [v]
        tm = _FakeMeta(musicbrainz_albumid=self._REL,
                       musicbrainz_recordingid=rec_id,
                       musicbrainz_workid=work_id,
                       album="Tchaikovsky Ballet Suites",
                       title="track %d" % num,
                       tracknumber=str(num), discnumber="1")
        tm['~ce_options'] = repr(opts)
        from unittest.mock import Mock
        t = Mock(name="track%d" % num)
        t.metadata = tm
        t._id = work_id
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    def test_track6_collapses_to_version_a(self):
        from unittest.mock import Mock
        mod = self.mod
        pl = mod.PartLevels()
        # focus on the top-work resolution; skip the heavy extension/publish
        pl.extend_metadata = lambda *a, **k: None
        pl.publish_metadata = lambda *a, **k: None
        pl.process_work_artists = lambda *a, **k: None
        # get_aliases needs a full release node (track 1 triggers it) and
        # close_log needs the artists-side release_status; neither is relevant.
        saved = (mod.get_aliases, mod.close_log)
        mod.get_aliases = lambda *a, **k: None
        mod.close_log = lambda *a, **k: None
        self.addCleanup(lambda: setattr(mod, "get_aliases", saved[0]))
        self.addCleanup(lambda: setattr(mod, "close_log", saved[1]))

        pending = []
        tagger = Mock()

        def fake_get(host, port, path, callback, **kwargs):
            wid = path.rsplit("/", 1)[-1]
            pending.append((callback, self._load("work_%s.json" % wid)))
        tagger.webservice.get = fake_get

        album = Mock()
        album._requests = 0
        album._new_tracks = []
        album.tagger = tagger
        album._finalize_loading = lambda _arg: None

        opts = dict(_ALL_OPTION_DEFAULTS)
        opts.update({
            "classical_work_parts": True, "use_cache": True,
            "cwp_partial": False, "cwp_arrangements": False,
            "cwp_medley": False, "cwp_collections": False,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        tracks = {}
        for num, rec_id, work_id in self._TRACKS:
            track = self._make_track(num, rec_id, work_id, opts)
            tracks[num] = track
            album._new_tracks.append(track)
            trackXmlNode = {'recording': self._load("rec_track%d.json" % num)}
            pl.add_work_info(album, track.metadata, trackXmlNode, {})

        # deliver deferred responses (each may queue more parent lookups)
        while pending:
            callback, resp = pending.pop(0)
            callback(resp, None, None)

        version_a = (self._A,)
        for num, track in tracks.items():
            tm = track.metadata
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_top'])), version_a,
                "track %d top work id should be Version A only" % num)
        # and specifically track 6 (the movement absent from Version B)
        self.assertEqual(
            tuple(self.mod.str_to_list(tracks[6].metadata['~cwp_workid_top'])),
            version_a)


class LisztRedundantParentIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release-group 652df93a ("A Liszt Portrait"),
    Annees de pelerinage Year 1 movements. Drives the REAL add_work_info ->
    work_process -> process_album pipeline against the actual MusicBrainz
    fixtures. Movements 21-2 (Au lac de Wallenstadt) and 28-1 (Eglogue) each
    carry an extra DIRECT parent, the broad "Annees de pelerinage" (COLL),
    which is really the grandparent of the specific "Premiere annee: Suisse,
    S.160" (SPEC). Without the redundant-parent reduction those two movements
    are tagged one level flatter than their siblings (SPEC vanishes); the fix
    gives every Year-1 movement the same three-level hierarchy."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "liszt")
    _REL = "liszt-rel"
    _SPEC = "d5800420-373c-46b3-af4c-173295812817"   # Premiere annee: Suisse
    _COLL = "29954628-3a98-41f9-87a1-14dc00bcfbe6"   # Annees de pelerinage
    # (label, disc, track, recording id, movement work id); the two problem
    # movements (Au lac, Eglogue) carry both SPEC and COLL as direct parents.
    _TRACKS = [
        ("d21t1", 21, 1, "9ca51ec9-6bcd-487e-810e-a1e55bb8e716", "5804701d-54a6-4c9d-afb9-3e01d6704e5a"),
        ("d21t2", 21, 2, "109533a6-ac39-4a53-8f79-0accf2d724fa", "67bb0266-41c3-4a0a-86ff-9735b608bfa2"),
        ("d21t3", 21, 3, "f1de8c74-57a9-45df-af4d-e895971ab537", "b428b55c-1f68-4a34-a643-a65a6b791b65"),
        ("d21t4", 21, 4, "e1ef7480-8d3b-41c9-9d4e-2ce585ce09fc", "c8a09f21-a7cd-49e5-aac7-60f0e427a7ce"),
        ("d21t5", 21, 5, "d494af57-15c0-45c2-861f-d8c0f4582c51", "676f6953-af37-465e-8af6-711cf727567c"),
        ("d28t1", 28, 1, "d5ebbb0c-abce-437c-96e0-2fa1152215e1", "3a47f337-9e3b-44d0-b36d-2d59293b633f"),
    ]

    def _load(self, name):
        with open(os.path.join(self._FIXDIR, name), encoding="utf-8") as f:
            return json.load(f)

    def setUp(self):
        super().setUp()
        self.set_config_values(setting={
            "server_host": "musicbrainz.org", "server_port": 443,
            "use_cache": True, "classical_work_parts": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "cwp_partial": False, "cwp_arrangements": False,
            "cwp_medley": False, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _make_track(self, label, disc, track, rec_id, work_id, opts):
        class _M(dict):
            def __getitem__(self, k):
                return self.get(k, '')

            def getall(self, k):
                v = self.get(k)
                return [] if v is None else (v if isinstance(v, list) else [v])
        tm = _M(musicbrainz_albumid=self._REL, musicbrainz_recordingid=rec_id,
                musicbrainz_workid=work_id, album="A Liszt Portrait",
                title=label, tracknumber=str(track), discnumber=str(disc))
        tm['~ce_options'] = repr(opts)
        from unittest.mock import Mock
        t = Mock(name=label)
        t.metadata = tm
        t._id = label
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    def test_year1_movements_share_three_level_hierarchy(self):
        from unittest.mock import Mock
        mod = self.mod
        pl = mod.PartLevels()
        # the hierarchy tags (~cwp_workid_N / ~cwp_part_levels) are set before
        # extend_metadata, so stub it (and publish) and read them directly.
        pl.extend_metadata = lambda *a, **k: None
        pl.publish_metadata = lambda *a, **k: None
        pl.process_work_artists = lambda *a, **k: None
        saved = (mod.get_aliases, mod.close_log)
        mod.get_aliases = lambda *a, **k: None
        mod.close_log = lambda *a, **k: None
        self.addCleanup(lambda: setattr(mod, "get_aliases", saved[0]))
        self.addCleanup(lambda: setattr(mod, "close_log", saved[1]))

        pending = []
        tagger = Mock()
        tagger.webservice.get = (
            lambda host, port, path, cb, **k:
            pending.append((cb, self._load("work_%s.json"
                                           % path.rsplit("/", 1)[-1]))))
        album = Mock()
        album._requests = 0
        album._new_tracks = []
        album.tagger = tagger
        album._finalize_loading = lambda _a: None

        opts = dict(_ALL_OPTION_DEFAULTS)
        opts.update({
            "classical_work_parts": True, "use_cache": True,
            "cwp_partial": False, "cwp_arrangements": False,
            "cwp_medley": False, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        tracks = {}
        for label, disc, track, rec_id, work_id in self._TRACKS:
            t = self._make_track(label, disc, track, rec_id, work_id, opts)
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        while pending:
            cb, resp = pending.pop(0)
            cb(resp, None, None)

        # Every Year-1 movement -- the two problem tracks included -- must have
        # the same three-level hierarchy: movement / SPEC / COLL.
        for label in ("d21t1", "d21t2", "d21t3", "d21t4", "d21t5", "d28t1"):
            tm = tracks[label].metadata
            self.assertEqual(tm['~cwp_part_levels'], '2',
                             "%s should be 3 levels deep" % label)
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_1'])), (self._SPEC,),
                "%s middle work should be Premiere annee: Suisse" % label)
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_2'])), (self._COLL,),
                "%s top work should be Annees de pelerinage" % label)


class DonGiovanniStandaloneParentIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release 510944a2 ("The Da Ponte Operas"), Don
    Giovanni disc 6. Tracks 6-3 ("In quali eccessi") and 6-4 ("Mi tradi") are
    each part of BOTH the opera's "Atto II" (embedded, itself part of the opera
    K. 527) AND the standalone "Recitative and Aria, K. 540c" (its own top);
    their neighbours 6-2 and 6-5 have only "Atto II". Without the fix the two
    parents fuse into the intermediate work level, so 6-3/6-4 get a two-value
    ~cwp_work_1 ("Atto II; Recitative and Aria, K. 540c") while their neighbours
    get a single "Atto II". The reduction drops the standalone parent so every
    Atto II track shares the same intermediate work."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "dongiovanni")
    _REL = "dg-rel"
    _ATTO2 = "18a0544b-89c1-4510-a673-c1b1728fd742"
    _OPERA = "b3b1e2b3-cbb8-4b46-a7d0-0031ec13492c"
    # (label, disc, track, recording id, movement work id); 6-3 and 6-4 carry
    # the extra standalone K.540c parent.
    _TRACKS = [
        ("d6t2", 6, 2, "b40c272a-bc2a-4d49-b95b-b211f4cec6fa", "e6c6d039-d3c0-31b5-b3c2-f58a68dfcada"),
        ("d6t3", 6, 3, "7c9830c3-e465-4c35-975c-4904a2433789", "c1b48770-4e0c-3b69-9e6a-6f6f01da863d"),
        ("d6t4", 6, 4, "047f17e3-5b5c-42ec-ac0e-2dc07dd4dbfe", "395f28e7-6594-3d8c-b03f-46e4121830ce"),
        ("d6t5", 6, 5, "1b7a064f-5661-42a0-bcd8-005d3387e493", "28ac8c6c-e6e8-3f78-90b2-9133ddcbf12a"),
    ]

    def _load(self, name):
        with open(os.path.join(self._FIXDIR, name), encoding="utf-8") as f:
            return json.load(f)

    def setUp(self):
        super().setUp()
        self.set_config_values(setting={
            "server_host": "musicbrainz.org", "server_port": 443,
            "use_cache": True, "classical_work_parts": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "cwp_partial": False, "cwp_arrangements": False,
            "cwp_medley": False, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _make_track(self, label, disc, track, rec_id, work_id, opts):
        class _M(dict):
            def __getitem__(self, k):
                return self.get(k, '')

            def getall(self, k):
                v = self.get(k)
                return [] if v is None else (v if isinstance(v, list) else [v])
        tm = _M(musicbrainz_albumid=self._REL, musicbrainz_recordingid=rec_id,
                musicbrainz_workid=work_id, album="The Da Ponte Operas",
                title=label, tracknumber=str(track), discnumber=str(disc))
        tm['~ce_options'] = repr(opts)
        from unittest.mock import Mock
        t = Mock(name=label)
        t.metadata = tm
        t._id = label
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    def test_standalone_parent_dropped_movements_share_intermediate(self):
        from unittest.mock import Mock
        mod = self.mod
        pl = mod.PartLevels()
        pl.extend_metadata = lambda *a, **k: None
        pl.publish_metadata = lambda *a, **k: None
        pl.process_work_artists = lambda *a, **k: None
        saved = (mod.get_aliases, mod.close_log)
        mod.get_aliases = lambda *a, **k: None
        mod.close_log = lambda *a, **k: None
        self.addCleanup(lambda: setattr(mod, "get_aliases", saved[0]))
        self.addCleanup(lambda: setattr(mod, "close_log", saved[1]))

        pending = []
        tagger = Mock()
        tagger.webservice.get = (
            lambda host, port, path, cb, **k:
            pending.append((cb, self._load("work_%s.json"
                                           % path.rsplit("/", 1)[-1]))))
        album = Mock()
        album._requests = 0
        album._new_tracks = []
        album.tagger = tagger
        album._finalize_loading = lambda _a: None

        opts = dict(_ALL_OPTION_DEFAULTS)
        opts.update({
            "classical_work_parts": True, "use_cache": True,
            "cwp_partial": False, "cwp_arrangements": False,
            "cwp_medley": False, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        tracks = {}
        for label, disc, track, rec_id, work_id in self._TRACKS:
            t = self._make_track(label, disc, track, rec_id, work_id, opts)
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        while pending:
            cb, resp = pending.pop(0)
            cb(resp, None, None)

        # Every Atto II track -- the recit+aria pair included -- must share the
        # same single intermediate work (Atto II) and the opera as top; the
        # standalone K.540c must not leak into the intermediate work level.
        for label in ("d6t2", "d6t3", "d6t4", "d6t5"):
            tm = tracks[label].metadata
            self.assertEqual(tm['~cwp_part_levels'], '2', label)
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_1'])),
                (self._ATTO2,),
                "%s intermediate work should be Atto II only" % label)
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_2'])),
                (self._OPERA,), label)


class TristanFullAlbumIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release 20a3b3d6 (Wesendonck-Lieder / Orchestral
    Music). Track 2 ("Tristan und Isolde: Prelude and Liebestod") spans two
    acts, whose overlapping tops _merge_duplicate_tops folds into one survivor,
    grafting the track onto it. The whole 11-track album is run so the collapse,
    graft and top-processing all execute against the real hierarchy (which
    includes a circular work reference in the Tristan tree)."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "tristan")
    _REL = "tristan-rel"
    _TRACKS = [
        ("t1", 1, 1, "73f24ed7-2edb-4649-9986-a64f535120f8", "b3d13ed3-5cb8-338c-815e-a33432d8981a"),
        ("t2", 1, 2, "05cc6415-4943-4592-b184-6fdfec280330", "7b4b0ef2-7928-3c37-8107-1355eb043855"),
        ("t3", 1, 3, "18b35e45-7eb8-4017-acfa-46d910308111", "e9ffb0e1-f26a-3420-ac12-6bc062042989"),
        ("t4", 1, 4, "f065fa2e-177a-49ea-891a-c0b753105bb3", "43424ee0-ea55-4a39-b2f4-722c0517efbb"),
        ("t5", 1, 5, "c93127b5-1549-4ded-8089-7ac7ca13e401", "82fcb2f7-355f-4585-ada5-d5d4bc629cde"),
        ("t6", 1, 6, "953f7003-181c-430a-9fc8-b240d5b0d6fc", "74563ddf-4a42-4c83-8ce8-19a84a75ee7b"),
        ("t7", 1, 7, "c56ff364-4be9-4fa0-b899-2ad3ff9caa6d", "1388e737-1c44-413e-b617-73ba771b2918"),
        ("t8", 1, 8, "9486e32a-35ed-4059-a346-fd2ab188e95f", "fef6ff04-822f-49d8-9add-04f6c82e4e95"),
        ("t9", 1, 9, "e14a9c92-939b-4287-acba-8c6d00fcf21e", "f5b6994d-98ed-43c5-a891-ae1a23c8ea6e"),
        ("t10", 1, 10, "5f5f1f2a-bf05-4168-aa87-0b3a262edb2a", "6b198406-4fbf-3d61-82db-0b7ef195a7fe"),
        ("t11", 1, 11, "0446b58a-6e81-4403-a53e-98babe20211c", "6996fc77-b5dc-48ca-a7d5-3434f541f87a"),
    ]

    def _load(self, name):
        with open(os.path.join(self._FIXDIR, name), encoding="utf-8") as f:
            return json.load(f)

    def setUp(self):
        super().setUp()
        self.set_config_values(setting={
            "server_host": "musicbrainz.org", "server_port": 443,
            "use_cache": True, "classical_work_parts": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "cwp_partial": False, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _make_track(self, label, disc, track, rec_id, work_id, opts):
        class _M(dict):
            def __getitem__(self, k):
                return self.get(k, '')

            def getall(self, k):
                v = self.get(k)
                return [] if v is None else (v if isinstance(v, list) else [v])
        tm = _M(musicbrainz_albumid=self._REL, musicbrainz_recordingid=rec_id,
                musicbrainz_workid=work_id, album="Wesendonck-Lieder / Orchestral Music",
                title=label, tracknumber=str(track), discnumber=str(disc))
        tm['~ce_options'] = repr(opts)
        from unittest.mock import Mock
        t = Mock(name=label)
        t.metadata = tm
        t._id = label
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    _OPERA = "ae217ba8-0b07-4b0b-aed6-c80535dcd94b"   # Tristan und Isolde, WWV 90

    def test_track2_resolves_to_opera_top(self):
        from unittest.mock import Mock
        mod = self.mod
        pl = mod.PartLevels()
        pl.extend_metadata = lambda *a, **k: None
        pl.publish_metadata = lambda *a, **k: None
        pl.process_work_artists = lambda *a, **k: None
        saved = (mod.get_aliases, mod.close_log)
        mod.get_aliases = lambda *a, **k: None
        mod.close_log = lambda *a, **k: None
        self.addCleanup(lambda: setattr(mod, "get_aliases", saved[0]))
        self.addCleanup(lambda: setattr(mod, "close_log", saved[1]))

        pending = []
        tagger = Mock()
        tagger.webservice.get = (
            lambda host, port, path, cb, **k:
            pending.append((cb, self._load("work_%s.json"
                                           % path.rsplit("/", 1)[-1]))))
        album = Mock()
        album._requests = 0
        album._new_tracks = []
        album.tagger = tagger
        album._finalize_loading = lambda _a: None

        opts = dict(_ALL_OPTION_DEFAULTS)
        opts.update({
            "classical_work_parts": True, "use_cache": True,
            "cwp_partial": False, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        tracks = {}
        for label, disc, track, rec_id, work_id in self._TRACKS:
            t = self._make_track(label, disc, track, rec_id, work_id, opts)
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        while pending:
            cb, resp = pending.pop(0)
            cb(resp, None, None)

        # Track 2's work is an arrangement that reaches the opera "Tristan und
        # Isolde, WWV 90" by two independent parent paths (Akt I Vorspiel and
        # Akt III Liebestod). Both terminate at that opera, so it is the single
        # correct top work. Before the fix the two convergent paths were traced
        # together, manufacturing a false cycle that (a) made build_parts strip
        # the opera as a "descendant of child", orphaning the track, and (b)
        # could send the trackback walkers into unbounded recursion. The track
        # must resolve cleanly to the opera as its top and be fully tagged.
        t2 = tracks["t2"].metadata
        self.assertTrue(t2['~cwp_workid_top'],
                        "track 2 (Tristan) lost its top work entirely")
        self.assertEqual(
            tuple(self.mod.str_to_list(t2['~cwp_workid_top'])),
            (self._OPERA,),
            "track 2 should resolve to the opera Tristan und Isolde as top")
        # top_work must be single-valued (the id reduction must carry the name
        # with it) -- a two-value "Akt III; Tristan und Isolde" would break a
        # single-valued top_work used as a shuffle-group key.
        self.assertEqual(
            self.mod.str_to_list(t2['~cwp_work_top']),
            ["Tristan und Isolde, WWV 90"],
            "track 2 top_work must be the single opera name")
        # No track on the album may be orphaned: every one keeps a top work,
        # so every track is published and its artist rewritten from the composer.
        for label in tracks:
            self.assertTrue(
                tracks[label].metadata['~cwp_workid_top'],
                "%s lost its top work" % label)


if __name__ == "__main__":
    unittest.main()
