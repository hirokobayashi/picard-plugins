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

    # ----- Album prefix: composer_album_prefix -----

    def test_composer_album_prefix_single_composer(self):
        """A single composer is prefixed onto the album title (the original
        composer-omitted use case)."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(
            prefix("Symphony no. 8", ["Bruckner"], False),
            "Bruckner: Symphony no. 8")

    def test_composer_album_prefix_multi_composer_default(self):
        """With the omit option off, all composers are prefixed (legacy
        behaviour), joined with '; ' in the order given."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(
            prefix("Great Symphonies", ["Bruckner", "Mahler"], False),
            "Bruckner; Mahler: Great Symphonies")

    def test_composer_album_prefix_omit_when_title_names_composer(self):
        """With the omit option on and >1 composer, the prefix is suppressed
        entirely when the title already names a composer."""
        prefix = self.mod.composer_album_prefix
        title = "Bruckner: Symphony no. 8 / Mendelssohn: Symphony no. 4"
        self.assertEqual(
            prefix(title, ["Bruckner", "Mendelssohn"], True),
            title)

    def test_composer_album_prefix_omit_any_composer_suppresses_all(self):
        """All-or-nothing: if any one composer already appears in the title,
        the whole prefix is dropped (so a missing composer is not prefixed
        alone)."""
        prefix = self.mod.composer_album_prefix
        title = "Bruckner: Symphony no. 8 & other works"
        self.assertEqual(
            prefix(title, ["Bruckner", "Wagner"], True),
            title)

    def test_composer_album_prefix_omit_but_title_has_no_composer(self):
        """With the omit option on but no composer named in the title, fall
        back to the standard prefixed format."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(
            prefix("Romantic Symphonies", ["Bruckner", "Mahler"], True),
            "Bruckner; Mahler: Romantic Symphonies")

    def test_composer_album_prefix_requires_colon_form(self):
        """A bare mention of a composer (no 'Composer:' colon form) does not
        count as the title naming the composer, so the prefix is still added."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(
            prefix("Music by Bruckner and Mahler", ["Bruckner", "Mahler"],
                   True),
            "Bruckner; Mahler: Music by Bruckner and Mahler")

    def test_composer_album_prefix_no_false_substring_match(self):
        """A last name embedded in another word before a colon (e.g. 'Bach' in
        'Offenbach:') does not trigger omission - the word boundary is
        required."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(
            prefix("Offenbach: Gaite parisienne", ["Bach", "Mahler"], True),
            "Bach; Mahler: Offenbach: Gaite parisienne")

    def test_composer_album_prefix_omit_ignores_single_composer(self):
        """The omit option only applies to multi-composer albums; a single
        composer is still prefixed even if named in the title."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(
            prefix("Bruckner: Symphony no. 8", ["Bruckner"], True),
            "Bruckner: Bruckner: Symphony no. 8")

    def test_composer_album_prefix_match_is_case_insensitive(self):
        """Composer detection in the title is case-insensitive."""
        prefix = self.mod.composer_album_prefix
        title = "BRUCKNER: Symphony / mendelssohn: Symphony"
        self.assertEqual(
            prefix(title, ["Bruckner", "Mendelssohn"], True),
            title)

    def test_composer_album_prefix_no_names_unchanged(self):
        """With no composer last names, the title is returned unchanged."""
        prefix = self.mod.composer_album_prefix
        self.assertEqual(prefix("Some Album", [], True), "Some Album")

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

    # ----- work_group: single-valued deterministic grouping/shuffle key -------
    # top_work stays faithful (multi-valued on a genuine tie); work_group takes
    # over the single-value role foobar2000's shuffle pattern needs.

    def test_select_work_group_name_unit(self):
        """Unit-level coverage of the work_group selection helper: always one
        value, deterministic on a tie."""
        select = self.mod.PartLevels._select_work_group_name
        # unique winner -> that name (== top_work)
        self.assertEqual(select(["A", "B"], {"A": 3, "B": 1}), "A")
        self.assertEqual(select(["B", "A"], {"A": 3, "B": 1}), "A")
        # tie -> exactly ONE value, lowest by normalised name (not a list)
        self.assertEqual(select(["B", "A"], {"A": 2, "B": 2}), "A")
        self.assertEqual(select(["A", "B"], {"A": 2, "B": 2}), "A")
        # tie with equal normalised names -> first in discovery order
        self.assertEqual(select(["work", "Work"], {"work": 2, "Work": 2}),
                         "work")
        # no votes -> single first name (never the raw list)
        self.assertEqual(select(["A", "B"], {}), "A")
        # single-name list -> that name
        self.assertEqual(select(["Only Work"], {"Only Work": 3}), "Only Work")

    def test_work_group_unique_winner_equals_top_work(self):
        """Unique vote winner: work_group is a single value equal to top_work
        (drop-in for the old single-valued top_work usage)."""
        pl = self._make_trackback_partlevels()
        votes = {self._BOLT_BALLET: 8, self._BOLT_SUITE: 6}
        workId = ("mov",)
        pl.parts[workId] = {"name": "mov"}
        top_info = {"id": ("suite_id", "ballet_id"),
                    "name": [self._BOLT_SUITE, self._BOLT_BALLET],
                    "levels": 1, "single": False, "votes": votes}
        track = self._make_fake_track("mov", "movement", 11)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(track, "alb")]}
        pl.process_trackback("test", "alb", trackback, 0, top_info)
        self.assertEqual(track.metadata.get("~cwp_work_group"), self._BOLT_BALLET)
        self.assertEqual(track.metadata.get("~cwp_work_group"),
                         track.metadata.get("~cwp_work_top"))

    def test_work_group_tie_single_while_top_work_multi(self):
        """The core of the feature: on a genuine tie top_work keeps BOTH names
        (faithful) while work_group is reduced to exactly one deterministic
        value, so tracks of the group are never split by the shuffle key."""
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
        # top_work faithful: both tied names kept
        self.assertEqual(track.metadata.get("~cwp_work_top"),
                         [self._BOLT_BALLET, self._BOLT_SUITE])
        # work_group: a single string, deterministic (lowest normalised name)
        group = track.metadata.get("~cwp_work_group")
        self.assertIsInstance(group, str)
        self.assertEqual(group, min([self._BOLT_BALLET, self._BOLT_SUITE],
                                    key=pl._normalise_name))

    def test_work_group_tie_consistent_across_shared_tracks(self):
        """Every track sharing a tied top yields the identical single
        work_group value regardless of the order its parent list happens to be
        stored in -- so foobar2000 groups them as one unit."""
        pl = self._make_trackback_partlevels()
        votes = {self._BOLT_BALLET: 6, self._BOLT_SUITE: 6}
        groups = []
        for order in ([self._BOLT_BALLET, self._BOLT_SUITE],
                      [self._BOLT_SUITE, self._BOLT_BALLET]):
            workId = ("mov" + str(len(groups)),)
            pl.parts[workId] = {"name": "mov"}
            top_info = {"id": tuple("id" + n[:3] for n in order),
                        "name": list(order), "levels": 1, "single": False,
                        "votes": votes}
            track = self._make_fake_track(workId[0], "movement", 11)
            trackback = {"id": list(workId), "depth": 0, "height": 1,
                         "meta": [(track, "alb")]}
            pl.process_trackback("test", "alb", trackback, 0, top_info)
            groups.append(track.metadata.get("~cwp_work_group"))
        self.assertEqual(groups[0], groups[1])

    def test_work_group_string_top_equals_top_work(self):
        """A single-work top (plain-string name): work_group == top_work."""
        pl = self._make_trackback_partlevels()
        workId = ("83e63350",)
        pl.parts[workId] = {"name": "Spiegel im Spiegel"}
        top_info = {"id": workId, "name": "Spiegel im Spiegel",
                    "levels": 1, "single": True}
        track = self._make_fake_track("9547dfb8", "Spiegel im Spiegel", 1)
        trackback = {"id": list(workId), "depth": 0, "height": 1,
                     "meta": [(track, "alb")]}
        pl.process_trackback("test", "alb", trackback, 0, top_info)
        self.assertEqual(track.metadata.get("~cwp_work_group"),
                         "Spiegel im Spiegel")
        self.assertEqual(track.metadata.get("~cwp_work_group"),
                         track.metadata.get("~cwp_work_top"))

    def test_set_metadata_fused_top_work_group_single(self):
        """For a fused multi-parent top, set_metadata writes the pre-resolved
        single work_group (not the joined parent list), staying in step with the
        resolved top_name."""
        pl = self._make_trackback_partlevels()
        joined = self._BOLT_BALLET + "; " + self._BOLT_SUITE
        pl.strip_parent_from_work = lambda *a, **k: ("stripped", joined)
        workId = ("mov",)
        parentId = ("ballet_id", "suite_id")
        pl.parts[workId] = {"name": "mov"}
        pl.parts[parentId] = {"name": [self._BOLT_BALLET, self._BOLT_SUITE],
                              "no_parent": True}
        track = self._make_fake_track("mov", "movement", 11)
        pl.set_metadata("test", 1, workId, parentId,
                        [self._BOLT_BALLET, self._BOLT_SUITE], track,
                        [self._BOLT_BALLET, self._BOLT_SUITE], self._BOLT_BALLET)
        self.assertEqual(track.metadata.get("~cwp_work_group"), self._BOLT_BALLET)
        self.assertNotIn("Suite from The Bolt",
                         track.metadata.get("~cwp_work_group"))

    def test_set_metadata_single_top_work_group_matches_top(self):
        """Single-work top (no resolved names): work_group == work_top =
        full_parent."""
        pl = self._make_trackback_partlevels()
        pl.strip_parent_from_work = lambda *a, **k: ("stripped", "Full Work Name")
        workId = ("w",)
        parentId = ("p",)
        pl.parts[workId] = {"name": "w"}
        pl.parts[parentId] = {"name": "Some Work", "no_parent": True}
        track = self._make_fake_track("w", "movement", 1)
        pl.set_metadata("test", 1, workId, parentId, "Some Work", track,
                        None, None)
        self.assertEqual(track.metadata.get("~cwp_work_group"), "Full Work Name")
        self.assertEqual(track.metadata.get("~cwp_work_group"),
                         track.metadata.get("~cwp_work_top"))


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


class PlaceRomanizeTestCase(ClassicalExtrasTestCase):
    """Phase 1 romanization of non-Latin recording places (crr_romanize_place):
    the pure alias-resolution helpers, the recording_session_tags name-override
    hook, and the async place-alias lookup wired through recording_process."""

    # ----- pure helpers: _locale_matches / pick_alias_name -----

    def test_locale_matches_exact_and_regional(self):
        m = self.mod._locale_matches
        self.assertTrue(m("en", "en"))
        self.assertTrue(m("en_US", "en"))
        self.assertTrue(m("en-GB", "en"))     # hyphen normalised
        self.assertFalse(m("eng", "en"))      # not a regional variant
        self.assertFalse(m(None, "en"))
        self.assertFalse(m("en", ""))

    def test_pick_alias_prefers_primary(self):
        aliases = [
            {"locale": "en", "primary": False, "name": "Secondary"},
            {"locale": "en", "primary": True, "name": "Primary"},
        ]
        self.assertEqual(self.mod.pick_alias_name(aliases, ["en"]), "Primary")

    def test_pick_alias_no_primary_takes_first(self):
        """Several English aliases, none primary -> first in MB order."""
        aliases = [
            {"locale": "en", "name": "First"},
            {"locale": "en", "name": "Second"},
        ]
        self.assertEqual(self.mod.pick_alias_name(aliases, ["en"]), "First")

    def test_pick_alias_skips_ended_and_respects_locale_order(self):
        aliases = [
            {"locale": "en", "ended": True, "name": "OldEnglish"},
            {"locale": "de", "primary": True, "name": "Deutsch"},
            {"locale": "en", "primary": True, "name": "English"},
        ]
        # preferred de wins over en
        self.assertEqual(self.mod.pick_alias_name(aliases, ["de", "en"]),
                         "Deutsch")
        # with only en wanted, the ended en alias is skipped -> the live one
        self.assertEqual(self.mod.pick_alias_name(aliases, ["en"]), "English")

    def test_pick_alias_none_when_no_match(self):
        aliases = [{"locale": "fr", "name": "French"}]
        self.assertIsNone(self.mod.pick_alias_name(aliases, ["en"]))

    # ----- pure resolver: resolve_place_name -----

    def test_resolve_latin_unchanged(self):
        r = self.mod.resolve_place_name
        self.assertEqual(r("Atlanta Symphony Hall", [], ["en"]),
                         "Atlanta Symphony Hall")

    def test_resolve_cyrillic_prefers_english_alias(self):
        r = self.mod.resolve_place_name
        aliases = [{"locale": "en", "primary": True,
                    "name": "Great Hall of the Moscow Conservatory"}]
        self.assertEqual(r("Большой зал", aliases, ["en"]),
                         "Great Hall of the Moscow Conservatory")

    def test_resolve_cyrillic_transliterates_without_alias(self):
        r = self.mod.resolve_place_name
        # no alias -> get_roman transliteration (still readable Latin)
        out = r("Москва", [], ["en"])
        self.assertTrue(self.mod.only_roman_chars(out), msg=out)
        self.assertNotEqual(out, "Москва")

    def test_resolve_japanese_kept_without_alias(self):
        """Japanese (non-Cyrillic) with no alias stays as-is: get_roman cannot
        transliterate it, and we must not mangle it."""
        r = self.mod.resolve_place_name
        self.assertEqual(r("サントリーホール", [], ["en"]), "サントリーホール")

    def test_resolve_japanese_uses_english_alias_when_present(self):
        r = self.mod.resolve_place_name
        aliases = [{"locale": "en", "primary": True, "name": "Suntory Hall"},
                   {"locale": "ja", "primary": True, "name": "サントリーホール"}]
        self.assertEqual(r("サントリーホール", aliases, ["en"]), "Suntory Hall")

    def test_resolve_greek_without_alias_kept(self):
        """Greek has no transliterator here; with no alias it stays original."""
        r = self.mod.resolve_place_name
        self.assertEqual(r("Ηρώδειο", [], ["en"]), "Ηρώδειο")

    # ----- recording_session_tags name_overrides hook -----

    def test_session_tags_name_overrides_by_id(self):
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "1979-01-01", "end": "1979-01-01",
             "place": {"id": "P1", "name": "Большой зал",
                       "area": {"id": "A1", "name": "Москва"}}},
        ]
        overrides = {"P1": "Great Hall", "A1": "Moscow"}
        tags = self.mod.recording_session_tags(relations, overrides)
        self.assertEqual(tags["recordingplace"], ["Great Hall"])
        self.assertEqual(tags["recordingcity"], ["Moscow"])
        self.assertEqual(tags["recordingsessions"],
                         ["Great Hall, Moscow (1979-01-01)"])

    def test_session_tags_without_overrides_unchanged(self):
        """The new param defaults to None -> byte-for-byte legacy behaviour."""
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "1979-01-01", "end": "1979-01-01",
             "place": {"id": "P1", "name": "Большой зал",
                       "area": {"id": "A1", "name": "Москва"}}},
        ]
        tags = self.mod.recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Большой зал"])
        self.assertEqual(tags["recordingcity"], ["Москва"])

    def test_collect_place_ids_only_non_latin(self):
        relations = [
            {"target-type": "place", "type": "recorded at",
             "place": {"id": "P1", "name": "Большой зал",
                       "area": {"id": "A1", "name": "Moscow"}}},   # Latin city
            {"target-type": "place", "type": "recorded at",
             "place": {"id": "P2", "name": "Symphony Hall",        # Latin venue
                       "area": {"id": "A2", "name": "サントリー"}}},
        ]
        ids = self.mod.PartLevels._collect_place_ids(relations)
        # only the non-Latin entities, unique, order preserved; each carries the
        # governing area id used for the country walk (venue -> its area; area
        # entity -> itself)
        self.assertEqual(ids, [("place", "P1", "Большой зал", "A1"),
                               ("area", "A2", "サントリー", "A2")])

    # ----- async: place lookup wired through recording_process -----

    class _FakeWS:
        def __init__(self):
            self.calls = []

        def get(self, host, port, path, handler, **kw):
            self.calls.append({"path": path, "handler": handler, "kw": kw})

    class _FakeTagger:
        def __init__(self, ws):
            self.webservice = ws

    class _FakeAlbum:
        def __init__(self, ws):
            self._requests = 0
            self.finalized = False
            self.tagger = PlaceRomanizeTestCase._FakeTagger(ws)

        def _finalize_loading(self, _arg):
            self.finalized = True

    class _FakeTrack:
        def __init__(self, metadata):
            self.metadata = metadata

    def _romanize_env(self):
        from picard.metadata import Metadata
        self.set_config_values(setting={
            "crr_romanize_place": True,
            "artist_locales": [],
            "artist_locale": "en",
            "server_host": "musicbrainz.org",
            "server_port": 443,
        })
        pl = self.mod.PartLevels()
        self._process_calls = []
        pl.process_album = lambda rid, alb: self._process_calls.append(rid)
        ws = self._FakeWS()
        album = self._FakeAlbum(ws)
        album._requests = 1
        tm = Metadata()
        tm['musicbrainz_albumid'] = 'relR'
        track = self._FakeTrack(tm)
        pl.options[track] = {'classical_work_parts': True,
                             'crr_romanize_place': True}
        return pl, tm, track, album, ws

    def test_romanize_defers_then_writes_after_lookups(self):
        pl, tm, track, album, ws = self._romanize_env()
        pl.recordings_queue.append('rec1', (track, album))
        full = {"relations": [
            {"target-type": "place", "type": "recorded at",
             "begin": "1979-05-01", "end": "1979-05-01",
             "place": {"id": "P1", "name": "Большой зал",
                       "area": {"id": "A1", "name": "Москва"}}},
        ]}
        pl.recording_process('rec1', 0, full, None, None)
        # deferred: nothing written yet, request still held, two lookups queued
        self.assertEqual(tm.getall('recording_place'), [])
        self.assertEqual(album._requests, 1)
        self.assertFalse(album.finalized)
        self.assertEqual(len(ws.calls), 2)
        paths = sorted(c['path'] for c in ws.calls)
        self.assertEqual(paths, ['/ws/2/area/A1', '/ws/2/place/P1'])

        # resolve the place lookup first (job still waits on the area)
        for c in ws.calls:
            if c['path'] == '/ws/2/place/P1':
                c['handler']({"aliases": [
                    {"locale": "en", "primary": True,
                     "name": "Great Hall of the Moscow Conservatory"}]},
                    None, None)
        self.assertEqual(tm.getall('recording_place'), [])   # not yet complete
        self.assertEqual(album._requests, 1)

        # resolve the area lookup -> job completes, tags written, finalized
        for c in ws.calls:
            if c['path'] == '/ws/2/area/A1':
                c['handler']({"aliases": [
                    {"locale": "en", "primary": True, "name": "Moscow"}]},
                    None, None)
        self.assertEqual(list(tm.getall('recording_place')),
                         ["Great Hall of the Moscow Conservatory"])
        self.assertEqual(list(tm.getall('recording_city')), ["Moscow"])
        self.assertEqual(tm['recording_session'],
                         "Great Hall of the Moscow Conservatory, Moscow "
                         "(1979-05-01)")
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)
        self.assertEqual(self._process_calls, ['relR'])

    def test_romanize_lookup_error_falls_back_to_original(self):
        """A failed place lookup must not hang: it falls back to the original
        name and the album still finalizes."""
        pl, tm, track, album, ws = self._romanize_env()
        pl.recordings_queue.append('rec2', (track, album))
        full = {"relations": [
            {"target-type": "place", "type": "recorded at",
             "begin": "1979-05-01", "end": "1979-05-01",
             "place": {"id": "P9", "name": "Ηρώδειο", "area": {}}},
        ]}
        pl.recording_process('rec2', 0, full, None, None)
        self.assertEqual(len(ws.calls), 1)
        ws.calls[0]['handler'](None, None, "503")   # lookup fails
        self.assertEqual(list(tm.getall('recording_place')), ["Ηρώδειο"])
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)

    def test_romanize_uses_cache_no_duplicate_lookup(self):
        """A venue already resolved on an earlier track is reused from cache
        with no new web request, and the recording completes immediately."""
        pl, tm, track, album, ws = self._romanize_env()
        pl.place_alias_cache[('place', 'P1')] = "Great Hall"
        pl.recordings_queue.append('rec3', (track, album))
        full = {"relations": [
            {"target-type": "place", "type": "recorded at",
             "begin": "1979-05-01", "end": "1979-05-01",
             "place": {"id": "P1", "name": "Большой зал", "area": {}}},
        ]}
        pl.recording_process('rec3', 0, full, None, None)
        self.assertEqual(len(ws.calls), 0)                 # no lookup fired
        self.assertEqual(list(tm.getall('recording_place')), ["Great Hall"])
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)

    # ----- Phase 2: keep venues from chosen countries as-is -----

    def test_keep_countries_parsing(self):
        pl = self.mod.PartLevels()
        t1, t2, t3 = object(), object(), object()
        pl.options[t1] = {'crr_keep_place_countries': 'jp, gr ; us'}
        self.assertEqual(pl._keep_countries(t1), {'JP', 'GR', 'US'})
        pl.options[t2] = {'crr_keep_place_countries': ''}
        self.assertEqual(pl._keep_countries(t2), set())
        pl.options[t3] = {}                     # option absent -> empty
        self.assertEqual(pl._keep_countries(t3), set())

    def test_area_parent_id(self):
        parent = {"relations": [
            {"target-type": "area", "type": "part of", "direction": "backward",
             "area": {"id": "PARENT"}}]}
        self.assertEqual(self.mod.PartLevels._area_parent_id(parent), "PARENT")
        # a forward part-of (this area contains another) is NOT the parent
        fwd = {"relations": [
            {"target-type": "area", "type": "part of", "direction": "forward",
             "area": {"id": "CHILD"}}]}
        self.assertIsNone(self.mod.PartLevels._area_parent_id(fwd))
        self.assertIsNone(self.mod.PartLevels._area_parent_id({"relations": []}))

    def _serve(self, ws, path, inc, response, error=None):
        """Invoke the captured handler for the ws call matching path+inc."""
        for c in ws.calls:
            if (c['path'] == path
                    and c['kw'].get('queryargs', {}).get('inc') == inc):
                c['handler'](response, None, error)
                return True
        return False

    def test_phase2_keeps_venue_in_listed_country(self):
        """Venue+city in Japan (keep-list JP): the country walk resolves to JP,
        so both names are kept in original script and NO alias lookup fires."""
        pl, tm, track, album, ws = self._romanize_env()
        pl.options[track]['crr_keep_place_countries'] = 'JP'
        pl.recordings_queue.append('recJP', (track, album))
        full = {"relations": [
            {"target-type": "place", "type": "recorded at",
             "begin": "2018-04-19", "end": "2018-04-19",
             "place": {"id": "P1", "name": "サントリーホール",
                       "area": {"id": "A1", "name": "赤坂"}}},
        ]}
        pl.recording_process('recJP', 0, full, None, None)
        # one area lookup fired for A1 (deduped across the two entities)
        self.assertTrue(self._serve(
            ws, '/ws/2/area/A1', 'area-rels',
            {"name": "赤坂", "relations": [
                {"target-type": "area", "type": "part of",
                 "direction": "backward", "area": {"id": "A2"}}]}))
        # walk recurses into the parent, which carries the country code
        self.assertTrue(self._serve(
            ws, '/ws/2/area/A2', 'area-rels',
            {"name": "Japan", "iso-3166-1-codes": ["JP"], "relations": []}))
        # kept in original script; no /place or /area aliases lookup happened
        self.assertEqual(list(tm.getall('recording_place')), ["サントリーホール"])
        self.assertEqual(list(tm.getall('recording_city')), ["赤坂"])
        alias_calls = [c for c in ws.calls
                       if c['kw'].get('queryargs', {}).get('inc') == 'aliases']
        self.assertEqual(alias_calls, [])
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)
        # country cached at both walked levels
        self.assertEqual(pl.area_country_cache['A1'], 'JP')
        self.assertEqual(pl.area_country_cache['A2'], 'JP')

    def test_phase2_romanizes_venue_outside_listed_country(self):
        """Venue in Russia while keep-list is JP: country resolves to RU (not
        kept), so the venue is romanized via an alias lookup after the walk."""
        pl, tm, track, album, ws = self._romanize_env()
        pl.options[track]['crr_keep_place_countries'] = 'JP'
        pl.recordings_queue.append('recRU', (track, album))
        full = {"relations": [
            {"target-type": "place", "type": "recorded at",
             "begin": "1979-05-01", "end": "1979-05-01",
             "place": {"id": "P1", "name": "Большой зал",
                       "area": {"id": "A1", "name": "Москва"}}},
        ]}
        pl.recording_process('recRU', 0, full, None, None)
        # country walk: A1 -> country RU
        self.assertTrue(self._serve(
            ws, '/ws/2/area/A1', 'area-rels',
            {"name": "Москва", "iso-3166-1-codes": ["RU"], "relations": []}))
        # RU not kept -> alias lookups now fire for the venue and the city
        self.assertTrue(self._serve(
            ws, '/ws/2/place/P1', 'aliases',
            {"aliases": [{"locale": "en", "primary": True,
                          "name": "Great Hall of the Moscow Conservatory"}]}))
        self.assertTrue(self._serve(
            ws, '/ws/2/area/A1', 'aliases',
            {"aliases": [{"locale": "en", "primary": True, "name": "Moscow"}]}))
        self.assertEqual(list(tm.getall('recording_place')),
                         ["Great Hall of the Moscow Conservatory"])
        self.assertEqual(list(tm.getall('recording_city')), ["Moscow"])
        self.assertEqual(album._requests, 0)
        self.assertTrue(album.finalized)


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

    def _run_full_album(self, drain="fifo"):
        """Drive the whole 11-track album through the real Picard flow (build
        every track, then drain the webservice callbacks so process_album runs
        once at the end) against the on-disk fixtures. Returns the dict of
        label -> track so callers can assert on the resulting metadata.

        ``drain`` selects the order the queued lookups are answered in; real
        Picard answers them in network-completion order -- see
        test_result_is_independent_of_lookup_order."""
        import random
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

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        return tracks

    def test_result_is_independent_of_lookup_order(self):
        """Track 2's work is an arrangement reaching the opera by two act-paths
        of different depths, so its parent chain is built from fused id tuples.
        _reduce_redundant_parents could shorten one of those tuples to a key no
        node was filed under; create_trackback then found no such parent, and
        because self.trackback is a defaultdict it appended a freshly created
        EMPTY node -- the whole subtree, and track 2 with it, dropped out of the
        album, leaving it with no top work at all.

        Whether the reduction fired depended on how much of the hierarchy had
        resolved when process_album ran, i.e. on the order the async lookups
        came back in: track 2 lost its top work in about two thirds of orders
        while FIFO -- the only order the other tests exercise -- got it right."""
        orders = ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]
        baseline = None
        for order in orders:
            tracks = self._run_full_album(drain=order)
            result = {
                label: self.mod.str_to_list(
                    tracks[label].metadata['~cwp_work_top'])
                for label, _d, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
                self.assertEqual(baseline["t2"], ["Tristan und Isolde, WWV 90"])
            else:
                self.assertEqual(
                    result, baseline,
                    "drain order %r changed the tags" % order)
            for label, top in result.items():
                self.assertTrue(
                    top, "%s lost its top work under drain order %r"
                    % (label, order))

    def test_track2_resolves_to_opera_top(self):
        tracks = self._run_full_album()

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

    def test_work_group_single_valued_across_real_album(self):
        """End-to-end: ~cwp_work_group is written for every track of the real
        album, is ALWAYS a single value (the foobar2000 shuffle-key guarantee),
        and equals ~cwp_work_top wherever top_work is itself single-valued (this
        album resolves each track to one top, so they match everywhere)."""
        tracks = self._run_full_album()
        for label in tracks:
            tm = tracks[label].metadata
            group = self.mod.str_to_list(tm['~cwp_work_group'])
            self.assertEqual(
                len(group), 1,
                "%s: work_group must be single-valued, got %r" % (label, group))
            top = self.mod.str_to_list(tm['~cwp_work_top'])
            if len(top) == 1:
                self.assertEqual(
                    group, top,
                    "%s: work_group should equal single-valued top_work" % label)
            else:
                # a genuine multi-valued top_work: work_group must be one of them
                self.assertIn(group[0], top,
                              "%s: work_group must be a top_work value" % label)

    def test_track2_work_group_is_single_opera(self):
        """Track 2's top_work is the single opera name; its work_group (the
        shuffle key) is the identical single value."""
        tracks = self._run_full_album()
        t2 = tracks["t2"].metadata
        self.assertEqual(
            self.mod.str_to_list(t2['~cwp_work_group']),
            ["Tristan und Isolde, WWV 90"],
            "track 2 work_group must be the single opera name")
        self.assertEqual(
            self.mod.str_to_list(t2['~cwp_work_group']),
            self.mod.str_to_list(t2['~cwp_work_top']))

class PlanetsBridgedTopsIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release c26d65ae (Holst: The Planets, with Colin
    Matthews' additions).

    Disc 1 tracks 1-7 are Holst's suite "The Planets, op. 32"; track 8 ("Pluto,
    the Renewer") is a movement of a DIFFERENT top work, "The Planets Suite
    extension". Track 7 (Neptune) is recorded as both suites' Neptune, so its
    top is the fused ('extension', 'op. 32') -- and that fusion is the only
    thing linking the two suites. _merge_duplicate_tops used to treat a whole
    transitively-connected component as one work, so the extension was folded
    away as a "duplicate" of op. 32 even though they share no work id; track 8,
    the extension's only exclusive track, was then left pointing at a dropped
    top, skipped by the tagging loop, and emerged with NO ~cwp_workid_top,
    ~cwp_work_top or ~cwp_work_group at all."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "planets")
    _REL = "c26d65ae-b64f-484e-84af-224b7fc6e0ac"
    _OP32 = "439c1605-bf74-4e0c-b2d9-6f4f89619ec6"      # The Planets, op. 32
    _EXT = "5a75e61c-a670-37a9-bf73-4e7e22bbb12a"       # The Planets Suite extension
    # (label, disc, track, recording id, work id(s))
    _TRACKS = [
        ("t1", 1, 1, "fedcd862-559e-479e-a565-5dddc21210ad", "9e00ce98-e460-4101-b2e3-8096a5c60c59"),
        ("t2", 1, 2, "2f97fba1-b1e7-4339-ace3-90dbcbd8cf7a", "3f86e70e-e428-46b1-834b-738243d37432"),
        ("t3", 1, 3, "5367bd55-5306-48d0-b71c-bc7a8730262d", "3efdc3db-5907-44fe-9639-68bcecaa6f0b"),
        ("t4", 1, 4, "81626a29-4147-4b23-9796-0ea6347c5606", "7b209915-be32-3f17-b2c5-b963964ed084"),
        ("t5", 1, 5, "e5389445-56f2-4cd6-8b3c-e1231b50eed5", "07d9e46d-0847-43d3-89da-27eb304943b1"),
        ("t6", 1, 6, "c213dd85-0641-4203-a75d-f1238cd35236", "e726a374-a823-436e-9afe-8f58d15eb547"),
        # Neptune: one recording, two works -- the op. 32 movement and the
        # extension's movement. This is the bridge between the two tops.
        ("t7", 1, 7, "c9d436aa-283f-4fda-bfe7-6aa187e366e1",
         ["254f397b-0d15-4812-aec4-02d0aa1d5898",
          "4be2ad86-562d-4ef0-970a-cebf777fb8cb"]),
        ("t8", 1, 8, "07be824f-0487-4e2e-bfc6-e184d0b60b04", "4477808e-cc63-3832-a884-d424e6df4363"),
        ("t9", 2, 1, "2cb2962b-67e2-4912-becf-a703e63f2d39", "e42cce08-f3f1-4e9b-8bfe-11670ad22d52"),
        ("t10", 2, 2, "6a6658b2-c3cf-4ca2-9b02-2fe85e79dbe0", "599168cc-8ca2-4685-9ef7-b178369a1ae0"),
        ("t11", 2, 3, "21240859-4f72-4b90-8662-aebf218afce4", "5c0deb15-2bc8-4ea6-9825-d42fbc87d86f"),
        ("t12", 2, 4, "04ee9933-7706-4d11-8b67-a749cc73f8b0", "570250af-fed3-4b19-9b9a-5ad42fbbad0f"),
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
                musicbrainz_workid=work_id, album="The Planets",
                title=label, tracknumber=str(track), discnumber=str(disc))
        tm['~ce_options'] = repr(opts)
        from unittest.mock import Mock
        t = Mock(name=label)
        t.metadata = tm
        t._id = label
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    def _run_full_album(self, drain="fifo"):
        """Drive all 12 tracks through the real Picard flow (build every track,
        then drain the webservice callbacks so process_album runs once at the
        end). Returns {label: track}.

        ``drain`` chooses the order the queued work lookups are answered in.
        Real Picard answers them in network-completion order, which is
        arbitrary, so "fifo" is only one of many orders the plugin must handle
        identically -- see test_result_is_independent_of_lookup_order."""
        import random
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

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        return tracks

    def test_no_track_is_orphaned(self):
        """Every track keeps a top work. Track 8 (Pluto) was the casualty: its
        only top was merged away as a bogus duplicate, so it lost all work
        metadata."""
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            self.assertTrue(
                tracks[label].metadata['~cwp_workid_top'],
                "%s lost its top work" % label)

    def test_pluto_keeps_the_extension_as_its_top_work(self):
        """The two suites share no work id, so they are distinct top works:
        Pluto stays under "The Planets Suite extension" rather than being
        absorbed into "The Planets, op. 32"."""
        t8 = self._run_full_album()["t8"].metadata
        self.assertEqual(
            tuple(self.mod.str_to_list(t8['~cwp_workid_top'])), (self._EXT,))
        self.assertEqual(
            self.mod.str_to_list(t8['~cwp_work_top']),
            ["The Planets Suite extension"])
        self.assertEqual(
            self.mod.str_to_list(t8['~cwp_work_group']),
            ["The Planets Suite extension"],
            "work_group is the shuffle key and must follow the real top work")

    def test_shared_neptune_resolves_to_the_voted_suite(self):
        """Track 7's fused ('extension', 'op. 32') top IS a genuine duplicate of
        op. 32 (it contains that id), so it is still merged in -- and op. 32,
        with six other tracks, wins the vote. Tracks 1-7 all report the one
        suite."""
        tracks = self._run_full_album()
        for label in ("t1", "t2", "t3", "t4", "t5", "t6", "t7"):
            tm = tracks[label].metadata
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_top'])),
                (self._OP32,),
                "%s should resolve to The Planets, op. 32" % label)
            self.assertEqual(
                self.mod.str_to_list(tm['~cwp_work_group']),
                ["The Planets, op. 32"],
                "%s work_group should be the op. 32 suite" % label)

    def test_work_group_single_valued_across_real_album(self):
        """The foobar2000 shuffle-key guarantee holds on this album: every
        track has exactly one work_group value, matching a single-valued
        top_work."""
        tracks = self._run_full_album()
        for label in tracks:
            tm = tracks[label].metadata
            group = self.mod.str_to_list(tm['~cwp_work_group'])
            self.assertEqual(
                len(group), 1,
                "%s: work_group must be single-valued, got %r" % (label, group))
            top = self.mod.str_to_list(tm['~cwp_work_top'])
            if len(top) == 1:
                self.assertEqual(
                    group, top,
                    "%s: work_group should equal single-valued top_work" % label)
            else:
                self.assertIn(group[0], top,
                              "%s: work_group must be a top_work value" % label)

    def test_result_is_independent_of_lookup_order(self):
        """The tags must not depend on the order the async work lookups happen
        to come back in. Picard answers them in network-completion order, so an
        order-sensitive result means the same release tags differently from run
        to run -- which is how this was reported: one run put all of tracks 1-8
        under "The Planets Suite extension", the next run got it right.

        The cause was work_process aliasing self.parts[new_ids] to the SAME dict
        as self.parts[prev_ids] when fusing a second parent onto a work, so
        writing the fused two-name list also overwrote the single-work top's
        name. Whichever of the two suites happened to resolve first was the one
        that got corrupted, so the vote between the two names flipped with
        timing."""
        orders = ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]
        baseline = None
        for order in orders:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(tracks[label].metadata['~cwp_work_top']),
                        self.mod.str_to_list(tracks[label].metadata['~cwp_work_group']))
                for label, _d, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
                # sanity: the baseline is the CORRECT answer, not just a stable
                # wrong one
                self.assertEqual(baseline["t8"][0],
                                 ["The Planets Suite extension"])
                self.assertEqual(baseline["t1"][0], ["The Planets, op. 32"])
            else:
                self.assertEqual(
                    result, baseline,
                    "drain order %r changed the tags" % order)


class MergeDuplicateTopsTestCase(ClassicalExtrasTestCase):
    """Unit-level cover for _merge_duplicate_tops: only tops that actually share
    a work id with the survivor are folded into it."""

    def _merge(self, tops, counts, names=None):
        """Run the merge over ``tops`` with ``counts`` = {top: n tracks}.
        ``names`` optionally overrides a top's work name (default: distinct per
        top). Returns the surviving self.top[album] list."""
        pl = self.mod.PartLevels()
        album = "alb"
        pl.top[album] = list(tops)
        names = names or {}
        pl.parts = {t: {'name': [names.get(t, 'w-%s' % t[0][:4])]}
                    for t in tops}
        tracks_in_top = {
            t: {("trk%d-%s" % (i, t[0]), album) for i in range(counts[t])}
            for t in tops}
        pl.trackback[album] = {}          # no trees: grafting is a no-op
        pl._merge_duplicate_tops("rel", album, tracks_in_top)
        return pl.top[album]

    def test_direct_duplicate_is_merged(self):
        """A fused top and one of its own constituents are the same work."""
        fused, part = ("a", "b"), ("a",)
        self.assertEqual(
            self._merge([fused, part], {fused: 1, part: 5}), [part])

    def test_bridged_distinct_tops_both_survive(self):
        """The Planets shape: ('ext',) and ('op32',) share no id and are only
        connected through the fused ('ext','op32') top of a shared movement.
        The fused top folds into the most-selected work; the other stays a top
        work in its own right rather than being deleted as a duplicate."""
        op32, fused, ext = ("op32",), ("ext", "op32"), ("ext",)
        self.assertEqual(
            self._merge([op32, fused, ext], {op32: 6, fused: 1, ext: 1}),
            [op32, ext])

    def test_unrelated_tops_untouched(self):
        """A genuine multi-work album (a concerto and a symphony) is preserved."""
        a, b = ("a",), ("b",)
        self.assertEqual(self._merge([a, b], {a: 3, b: 4}), [a, b])

    def test_bridged_same_named_tops_are_merged(self):
        """The Swan Lake shape: two editions of one ballet, entered under
        separate ids but the SAME title, bridged by the fused top of the
        movements that are part of both. Sharing no id, the round loop used to
        leave both standing, so the album reported two top works with identical
        names. They are the one work, so the component folds into the
        most-selected id."""
        orig, fused, drigo = ("orig",), ("orig", "drigo"), ("drigo",)
        name = "Swan Lake, op. 20"
        self.assertEqual(
            self._merge([orig, fused, drigo],
                        {orig: 14, fused: 0, drigo: 29},
                        names={orig: name, fused: name, drigo: name}),
            [drigo])

    def test_same_name_alone_does_not_merge(self):
        """Name equality only applies WITHIN a component, and components are
        built from shared work ids. Two same-named works with no fused top
        bridging them (two different composers' Requiem) share no id, so they
        are separate components and both survive."""
        a, b = ("a",), ("b",)
        self.assertEqual(
            self._merge([a, b], {a: 3, b: 4},
                        names={a: "Requiem", b: "Requiem"}),
            [a, b])



class GayneSceneLevelTopWorkIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release 52a8033b (Khachaturian: Gayne, 2 discs).

    Most tracks are linked to a leaf movement work, but three are linked
    straight at a *container* work instead: disc 1 track 12 to "Act I, Scene II
    (Recovery)" and disc 2 track 7 to "Act II, Scene V (Love)" (disc 1 tracks
    4/5 share one leaf). Those scene works are also intermediate nodes in their
    neighbours' hierarchies, so the plugin sees the same node both as a track's
    own work and as a parent of other tracks' works.

    Reported against d1t12, whose top work came out as the scene itself rather
    than the ballet. The MusicBrainz cause was that "Act I, Scene II (Recovery)"
    (and "Act I, Scene I (Spring)") had no "part of" link to "Act I", making
    them parentless roots; both have since been linked. This test pins the
    resolved hierarchy so a regression -- in the plugin or in a refetch of the
    fixtures -- is caught: every track on the album must report the ballet as
    its top work, and the scene-linked tracks must sit at the right depth."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "gayne")
    _REL = "52a8033b-562d-4f2d-9663-eda85e102775"
    _BALLET = "693e681e-98a1-4d21-9bc8-e084a248461d"   # Gayaneh, op. 50
    _ACT1 = "9ffd930d-eb52-4c0a-8ee4-551a5d7c32ad"     # Gayaneh: Act I
    _SCENE2 = "54f203c0-b992-40da-a318-a35310f887bb"   # Act I, Scene II (Recovery)
    # (label, disc, track, recording id, work id)
    _TRACKS = [
        ("d1t1", 1, 1, "9eecb165-6c44-4093-a116-1924b25dda9e",
         "dbb66a91-1290-4e01-a8c4-2928487a6494"),
        ("d1t2", 1, 2, "c77edf31-bd7d-4cd0-94cb-914718d626cb",
         "94859fab-d983-4cde-802f-8cb11a5c9d07"),
        ("d1t3", 1, 3, "c6dadf43-8250-4609-9a70-4903f55cf99b",
         "0c04b182-09ec-4c3c-b499-0680a5063bd3"),
        ("d1t4", 1, 4, "4a050941-29ba-4fd6-ac5e-bb5171ba72ec",
         "a0bb5503-3f9a-4359-8f11-c27d4d518b82"),
        ("d1t5", 1, 5, "867c15f1-ee10-4730-ab36-24f2340dfec6",
         "a0bb5503-3f9a-4359-8f11-c27d4d518b82"),
        ("d1t6", 1, 6, "74142639-a3cb-4014-aef9-52b4c3beb303",
         "af1f7eea-35bd-4d04-9d8a-3b1497afec88"),
        ("d1t7", 1, 7, "85fe59ba-86ed-4d37-b074-e0433a89ce57",
         "aebdbf15-ef7c-4729-bc35-002d7233864c"),
        ("d1t8", 1, 8, "ed682416-8e56-44f6-aa84-c0292f57b793",
         "6035f51e-0559-4444-9310-cbc3b79a960e"),
        ("d1t9", 1, 9, "87fe8719-162a-43ec-b5bc-638dc65a1d0a",
         "f5c29d63-43f8-420b-bd8d-97a2dc9defb0"),
        ("d1t10", 1, 10, "795afa97-4c76-4543-ab38-ae43876be4b4",
         "fb479c25-21be-4fba-aaa5-78693fcaafdd"),
        ("d1t11", 1, 11, "18abc847-4fc9-4395-afb7-9146d2200e8c",
         "8b1941d9-0bdd-408e-8d6a-e96b6a52dfc9"),
        ("d1t12", 1, 12, "2f741d26-cfdd-44c4-b582-8463ec0a36ab",
         "54f203c0-b992-40da-a318-a35310f887bb"),
        ("d1t13", 1, 13, "1825a0fc-d666-4729-802c-b71fd6f7fac8",
         "85878830-b5a9-483b-abdd-f1f2819cd754"),
        ("d1t14", 1, 14, "3b2d9191-6571-438e-9b27-1fecabea072a",
         "85878830-b5a9-483b-abdd-f1f2819cd754"),
        ("d1t15", 1, 15, "d3d1b6bf-8214-4421-bc41-59ddae91482d",
         "ffc2f12f-a6f4-45cc-a8b1-dd44a2df5c7f"),
        ("d1t16", 1, 16, "dc3d407c-684a-4074-8169-33a53082d517",
         "6f771810-5e09-4f8a-ab3f-f28bc6328da1"),
        ("d1t17", 1, 17, "0ddafb49-6191-4c7a-8039-de9b007ac9f2",
         "32393107-f780-4242-a747-03856de55aaf"),
        ("d1t18", 1, 18, "7daf5829-4e38-4344-9d59-d214cb4b29a1",
         "4f06bae9-ed7b-474f-9fd1-aedb613a5131"),
        ("d1t19", 1, 19, "a4d7b324-6d66-418f-b688-e7dfb34d02e2",
         "b89c6302-e6a4-4627-b6d7-838050ad3e8d"),
        ("d1t20", 1, 20, "aa8a6144-78be-4fc6-bfac-1e7886c27a7f",
         "a4222150-2bb1-4a7d-b402-80064b93b1ca"),
        ("d1t21", 1, 21, "32b68003-21f8-400c-b3d4-174d10b42270",
         "e3bead1c-c1ff-4d48-ba46-38fb4f60d549"),
        ("d1t22", 1, 22, "630d053a-0893-4080-8a02-0bfff4fcdc03",
         "64ae2dc6-b2e6-4ee5-add7-108328f553b9"),
        ("d1t23", 1, 23, "1c852c21-bfb0-49d3-b14d-99bfa67ed182",
         "4b4d382b-6994-4a44-87ed-7a9cd7bfeac8"),
        ("d1t24", 1, 24, "fb2d1a59-a08f-49f3-9598-83929c61913c",
         "30205ad2-bcff-4757-8a1b-c2c4220d63f0"),
        ("d2t1", 2, 1, "1d6f95fc-fa07-4666-8a9f-5f7417606406",
         "25dbb630-2019-4daf-a26c-2d75a5e11b45"),
        ("d2t2", 2, 2, "d3220710-5d37-482d-af89-f74c86f2f399",
         "0e776558-af02-4bdd-8d5f-8a0313eddf41"),
        ("d2t3", 2, 3, "8707181c-15f5-497e-b4c1-ac849b082b33",
         "77995ffa-460f-48b3-8a25-f364e6d6ca6f"),
        ("d2t4", 2, 4, "25f2f3f8-2d59-4930-9d08-bc12d1240381",
         "55fcadd4-cec1-4c5c-8e64-ccf4bc631570"),
        ("d2t5", 2, 5, "958764ae-7c0d-4ccf-bf09-bf009b86df46",
         "a898bf24-8552-46f1-a835-d3a3e51ca40b"),
        ("d2t6", 2, 6, "d069f937-8477-4aea-80a8-5daf52f3a92e",
         "64ef66f8-8623-4365-bc2f-fd27ae1bd426"),
        ("d2t7", 2, 7, "17f88bf3-996a-432e-bcb5-f177083fc792",
         "cf1ed388-904b-4992-a056-76f9ecea6612"),
        ("d2t8", 2, 8, "878dc738-a64a-40a2-bb2c-8d695488fd4b",
         "28b92f66-9d35-46ed-9aca-ce12ee3625e0"),
        ("d2t9", 2, 9, "3b84970c-559a-4680-991e-6560e1b2114b",
         "51c7cd70-1ae8-4ee2-a981-6e9ecad4db64"),
        ("d2t10", 2, 10, "09fedfc4-b336-46de-90aa-315c153aea14",
         "bd64cb18-353a-4ca9-959a-6423f1a02fc5"),
        ("d2t11", 2, 11, "8b08f65c-61e0-4f59-9faa-1dce7bdd6956",
         "e520e450-6c09-46f1-be8c-5bff9d728a32"),
        ("d2t12", 2, 12, "e651039a-1995-406b-be02-35205f44dac4",
         "1b33d9d1-3ec8-4155-b20f-be225d59b64b"),
        ("d2t13", 2, 13, "8dfe6c45-a06d-4906-bf6d-4c1ab7c38836",
         "f9d6ed83-55f9-480a-8e58-ec72463441e1"),
        ("d2t14", 2, 14, "c1439bed-4460-4521-b4f1-9b796b3ff2c0",
         "f9d6ed83-55f9-480a-8e58-ec72463441e1"),
        ("d2t15", 2, 15, "53e1d322-799c-4bcf-9ab0-c2b1c8ef7e7a",
         "b6171611-c838-4384-9b45-eb0055e41d2b"),
        ("d2t16", 2, 16, "2df8689b-c065-4124-9fec-873a6b1c5629",
         "0d9a1d30-3554-44e5-9206-d8f202ca3f58"),
        ("d2t17", 2, 17, "9cbd0f45-bebd-4edd-82ba-b5c6795d9ca1",
         "d534c014-83e5-4187-b871-4e4c6e714516"),
        ("d2t18", 2, 18, "58106303-f34f-4686-a496-6b8b7216cbbe",
         "a69996f6-1597-328c-bb27-ffbf0efdba6b"),
        ("d2t19", 2, 19, "a4238b68-2885-4b20-9b8e-8971992b2189",
         "327d8b52-9c79-4dba-8870-8063e95cc4ed"),
        ("d2t20", 2, 20, "df16eda9-42b3-422a-8b73-f6860e232ac9",
         "f11929a3-1a7c-45b4-9352-2a0624160dee"),
        ("d2t21", 2, 21, "cb3fd077-35e6-48de-add1-14825783f02e",
         "e3a17187-8df1-449b-a154-6a9d4f2a51aa"),
        ("d2t22", 2, 22, "560897c5-695e-4924-9484-0be2a95100f7",
         "0c9f5f62-c4d0-43bb-822a-e7f0c5b296cf"),
        ("d2t23", 2, 23, "665afb85-40b0-4de5-ab29-6774fbe2d47f",
         "b04bc4fa-24da-4ebf-91f7-3e4f0851224b"),
        ("d2t24", 2, 24, "270d655c-e4eb-474b-a340-4449811738e0",
         "908914da-eae3-4ec3-9a45-8f66ed730381"),
        ("d2t25", 2, 25, "0aeec881-06f8-45e5-9e6e-2a3f86f0fbcb",
         "c912e891-de3e-4864-934c-efcace339e90"),
        ("d2t26", 2, 26, "cea3bb69-e95c-43d0-90fd-7ae96e8ffe28",
         "ae240c62-610c-4404-b12b-1cec623d0b48"),
        ("d2t27", 2, 27, "c05f98e1-c1ad-4127-8c72-dc0012c1ff98",
         "9b6a0ecc-685c-4adf-a1ca-4070d0974cb0"),
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
                musicbrainz_workid=work_id, album="Gayne",
                title=label, tracknumber=str(track), discnumber=str(disc))
        tm['~ce_options'] = repr(opts)
        from unittest.mock import Mock
        t = Mock(name=label)
        t.metadata = tm
        t._id = label
        t.__hash__ = lambda self: hash(self._id)
        t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
        return t

    def _run_full_album(self, drain="fifo"):
        """Drive all 51 tracks through the real Picard flow: build every track,
        then drain the queued webservice callbacks so process_album runs once at
        the end. ``drain`` picks the order the lookups are answered in."""
        import random
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

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        self._pl = pl
        return tracks

    def test_descendant_listing_does_not_blow_up(self):
        """Guard against the descendant-propagation blowup this album exposed.

        work_process records, for each parent, every descendant reachable
        below it. That listing was a plain list appended to on every visit, so
        re-visiting a work re-appended its whole descendant list to the
        parent's -- self-concatenation that doubles on each pass. On this
        51-track ballet an unlucky lookup order grew one listing past 477
        MILLION entries holding just 19 distinct ids, and the album never
        finished tagging: Picard span forever, eating memory.

        The listing is only ever membership-tested, so it is a set and the
        propagation is idempotent. Asserted directly because the symptom is a
        hang, which no assertion would otherwise catch -- the suite would just
        stop."""
        self._run_full_album(drain="rand1")
        listing = self._pl.child_listing
        for parent, descendants in listing.items():
            self.assertIsInstance(
                descendants, set,
                "child_listing[%r] must be a set to stay idempotent" % (parent,))
            self.assertNotIn(
                parent, descendants,
                "%r recorded as its own descendant" % (parent,))
        biggest = max((len(v) for v in listing.values()), default=0)
        # The whole album is 58 works, so no parent can legitimately have more
        # descendants than that; the bug produced eight-figure counts.
        self.assertLessEqual(
            biggest, 58,
            "descendant listing has grown beyond the album's work count")

    def test_scene_linked_track_gets_the_ballet_as_top_work(self):
        """The reported failure: d1t12 is linked directly at "Act I, Scene II
        (Recovery)", which used to be a parentless work and so became its own
        top. It must resolve up through "Act I" to the ballet."""
        tm = self._run_full_album()["d1t12"].metadata
        self.assertEqual(
            tuple(self.mod.str_to_list(tm['~cwp_workid_top'])), (self._BALLET,))
        self.assertEqual(
            self.mod.str_to_list(tm['~cwp_work_top']), ["Gayaneh, op. 50"])
        self.assertEqual(
            self.mod.str_to_list(tm['~cwp_work_group']), ["Gayaneh, op. 50"])

    def test_whole_album_shares_one_top_work(self):
        """A single-work release: all 51 tracks belong to the one ballet, so no
        track may be orphaned or land on a scene/act as its own top."""
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            tm = tracks[label].metadata
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_top'])),
                (self._BALLET,),
                "%s should resolve to the ballet, got %r"
                % (label, tm['~cwp_work_top']))
            self.assertEqual(
                self.mod.str_to_list(tm['~cwp_work_group']),
                ["Gayaneh, op. 50"],
                "%s work_group should be the ballet" % label)

    def test_scene_linked_track_keeps_the_scene_as_its_own_work(self):
        """d1t12's own work stays the scene, sitting one level under Act I --
        the fix must lift its top, not flatten the track into the act."""
        tm = self._run_full_album()["d1t12"].metadata
        self.assertEqual(
            tuple(self.mod.str_to_list(tm['~cwp_workid_0'])), (self._SCENE2,))
        self.assertEqual(
            tuple(self.mod.str_to_list(tm['~cwp_workid_1'])), (self._ACT1,))
        self.assertEqual(tm['~cwp_part_levels'], '2')

    def test_result_is_independent_of_lookup_order(self):
        """Picard answers the work lookups in network-completion order, so the
        tags must not depend on which order this album's 50-odd lookups come
        back in."""
        orders = ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]
        baseline = None
        for order in orders:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(tracks[label].metadata['~cwp_work_top']),
                        self.mod.str_to_list(tracks[label].metadata['~cwp_work_group']))
                for label, _d, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
                self.assertEqual(baseline["d1t12"][0], ["Gayaneh, op. 50"])
            else:
                self.assertEqual(
                    result, baseline,
                    "drain order %r changed the tags" % order)


class DaphnisPartialRecordingCrashTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release 99feb608 (Ravel: Daphnis et Chloe), which
    aborted tagging outright when "include partial recordings" was on.

    MusicBrainz allows two relations between one recording and one work, and
    this release uses that: most recordings carry BOTH a 'partial' performance
    relation and a plain one. Several (d1t7, d1t9, d1t10) point both relations
    at the SAME work.

    That broke node identity twice. A node is keyed by its id TUPLE, so:

    1. build_work_info de-duplicated work NAMES but not work IDS, so the two
       relations appended the same id twice and the track's node was keyed
       ('X', 'X') -- a different node from ('X',), which is the only spelling
       anything else builds.
    2. work_process then renames a node in place as its own parents turn up,
       so ('X',) becomes ('X', 'X-parent'). A child still names its parent by
       the old tuple, and the bare .index(prev_ids) raised ValueError straight
       out of the webservice callback, abandoning the release.

    With cwp_partial off the album was always fine, which is why this needed
    the option on to reproduce.

    Fixing the crash then exposed a third defect underneath it: work_process
    renamed a parent node in work_listing in place, but that key is SHARED by
    every child with the same parent set, so the rename stranded the siblings
    and three tracks emitted no work tags at all. work_listing is additive
    now, matching what self.parts already did."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "daphnis")
    _REL = "99feb608-f52c-4c7c-a0ed-2d9d3200e3f9"
    _BALLET = "a55d2dfe-fb81-42e8-9e0f-3e6fd9654ca1"    # Daphnis et Chloe
    # Tracks whose two relations both point at the same work.
    _DUPLICATE_RELATION_TRACKS = ("d1t1", "d1t2", "d1t7", "d1t9", "d1t10")
    # (label, disc, track, recording id, work id(s))
    _TRACKS = [
        ('d1t1', 1, 1, '03e53458-39f8-482a-b470-80a5a70d15bf',
         '210c1890-db08-324d-a0b4-fc09c388152f'),
        ('d1t2', 1, 2, '8a7fa542-9262-4a4b-b999-bf29444e7aa1',
         '210c1890-db08-324d-a0b4-fc09c388152f'),
        ('d1t3', 1, 3, 'ac277cd8-f804-409c-8962-f1bc1474eebf',
         '12b7d2c7-2c8d-375f-9e3c-151cf875c781'),
        ('d1t4', 1, 4, '1b5d9fd9-dfaf-4e3b-9442-4fabb00022a0',
         '5d951041-56ed-3731-9994-709e8218f471'),
        ('d1t5', 1, 5, '3d38f2bc-9b5d-46a6-a7d8-fa5c803284ef',
         '70e3948f-db7c-3b31-95e4-70baa564a393'),
        ('d1t6', 1, 6, '54d756d9-1391-4710-a134-29c535fa4305',
         ['c757f3a9-6f6c-3e06-af1a-04f26ff7b36e', '70e3948f-db7c-3b31-95e4-70baa564a393']),
        ('d1t7', 1, 7, '13ae0661-1a3c-47ac-9b2c-85cca4c1def7',
         'c757f3a9-6f6c-3e06-af1a-04f26ff7b36e'),
        ('d1t8', 1, 8, '950e84f8-5ce2-4ec0-9460-723896364f96',
         ['1cebc96d-b4ed-3615-90d5-dbc26c381fb2', 'c757f3a9-6f6c-3e06-af1a-04f26ff7b36e']),
        ('d1t9', 1, 9, '763e80a6-d603-4c77-abc6-a07c20c70553',
         '1cebc96d-b4ed-3615-90d5-dbc26c381fb2'),
        ('d1t10', 1, 10, 'bd9fcffb-0eb5-4f75-9fc8-826868525e24',
         '1cebc96d-b4ed-3615-90d5-dbc26c381fb2'),
        ('d1t11', 1, 11, '65a4ff28-8cdd-4403-9bf2-e0e8bae67494',
         ['1cebc96d-b4ed-3615-90d5-dbc26c381fb2', 'aaaff290-f883-368f-bb5d-91e76001c734']),
        ('d1t12', 1, 12, '10bb3689-09a8-4e2d-a475-cd457105af7b',
         ['aaaff290-f883-368f-bb5d-91e76001c734', 'bcf2209c-108d-3e53-a347-89df0a88f9e4']),
        ('d1t13', 1, 13, '24c23dba-232b-4e40-8443-3992edc5e697',
         'e405c071-b184-32b8-aaae-7f5816418977'),
        ('d1t14', 1, 14, '7494b3ac-59b4-4beb-a275-f5dae57931df',
         'e405c071-b184-32b8-aaae-7f5816418977'),
        ('d1t15', 1, 15, 'e5b6e25f-b327-45b2-b855-a973cac9dc18',
         '9554228e-79e2-33df-a9af-5fa96f69a28c'),
        ('d1t16', 1, 16, '59997839-dbae-4350-8739-f83a11c0eb12',
         '7ee7f3db-c438-3fa5-a30b-ca40499c97d6'),
        ('d1t17', 1, 17, '2a025187-c9b8-4bf8-9609-f0c96307ffdf',
         '7ee7f3db-c438-3fa5-a30b-ca40499c97d6'),
        ('d1t18', 1, 18, '8d5613ab-b5f8-451c-98c3-b771ebca1280',
         '7ee7f3db-c438-3fa5-a30b-ca40499c97d6'),
        ('d1t19', 1, 19, '1a6bfc9f-0160-4c49-a684-588d8bb71841',
         '6f8e29d3-d405-3396-a667-7c15274b5a67'),
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
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _run_full_album(self, drain="fifo", partial=True):
        """Drive all 19 tracks through the real Picard flow. ``partial`` sets
        cwp_partial: the crash only happens with it on."""
        import random
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
            "cwp_partial": partial, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        class _M(dict):
            def __getitem__(self, k):
                return self.get(k, '')

            def getall(self, k):
                v = self.get(k)
                return [] if v is None else (v if isinstance(v, list) else [v])

        tracks = {}
        for label, disc, track, rec_id, work_id in self._TRACKS:
            tm = _M(musicbrainz_albumid=self._REL,
                    musicbrainz_recordingid=rec_id,
                    musicbrainz_workid=work_id, album="Daphnis et Chloe",
                    title=label, tracknumber=str(track), discnumber=str(disc))
            tm['~ce_options'] = repr(opts)
            t = Mock(name=label)
            t.metadata = tm
            t._id = label
            t.__hash__ = lambda self: hash(self._id)
            t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        self._pl = pl
        self._album = album
        return tracks

    def test_album_with_partial_recordings_does_not_crash(self):
        """The reported bug. work_process raised ValueError from inside the
        webservice callback, so Picard abandoned the release."""
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]:
            try:
                self._run_full_album(drain=order)
            except ValueError as e:
                self.fail("drain order %r crashed the album: %s" % (order, e))

    def test_node_ids_are_never_repeated_within_a_tuple(self):
        """Two relations to one work must not spell the node ('X', 'X'):
        nothing else ever builds that key, so the node is unreachable and its
        id tuple disagrees in length with its (already de-duplicated) name
        list."""
        self._run_full_album()
        for node in self._pl.work_listing[self._album]:
            self.assertEqual(
                len(node), len(set(node)),
                "node %r repeats a work id" % (node,))

    def test_all_tracks_resolve_to_the_ballet(self):
        """This is a single-work release: all 19 tracks report the ballet."""
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            tm = tracks[label].metadata
            self.assertEqual(
                tuple(self.mod.str_to_list(tm['~cwp_workid_top'])),
                (self._BALLET,),
                "%s should resolve to Daphnis et Chloe" % label)
            self.assertEqual(
                self.mod.str_to_list(tm['~cwp_work_group']),
                ["Daphnis et Chloé"],
                "%s work_group should be the ballet" % label)

    def test_album_is_clean_without_partial_recordings(self):
        """With cwp_partial off the album has always been correct and stable;
        pinned so the partial-recording work above cannot regress it."""
        baseline = None
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(6)]:
            tracks = self._run_full_album(drain=order, partial=False)
            result = {}
            for label, _d, _t, _r, _w in self._TRACKS:
                tm = tracks[label].metadata
                self.assertEqual(
                    tuple(self.mod.str_to_list(tm['~cwp_workid_top'])),
                    (self._BALLET,), "%s (order %s)" % (label, order))
                result[label] = (self.mod.str_to_list(tm['~cwp_work_top']),
                                 tm['~cwp_part_levels'])
            if baseline is None:
                baseline = result
            else:
                self.assertEqual(result, baseline,
                                 "drain order %r changed the tags" % order)

    def test_every_track_keeps_its_work_metadata(self):
        """No track may be stranded. d1t7, d1t9 and d1t10 -- the tracks whose
        two relations both name the same work -- used to come out with no work
        tags at all when cwp_partial was on, even though process_album assigned
        all 19 tracks the correct top work internally.

        Their parent node had been renamed in place as its own parents were
        discovered, so ('c757f3a9',) and ('1cebc96d',) stopped being keys in
        work_listing. That key is SHARED by every child with the same parent
        set, so the rename stranded the siblings: create_trackback found no
        such parent and (trackback being a defaultdict) grafted an EMPTY node,
        dropping the subtree. work_listing is additive now, as self.parts
        already was."""
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            self.assertTrue(
                tracks[label].metadata['~cwp_workid_top'],
                "%s lost its work metadata" % label)
            self.assertTrue(
                tracks[label].metadata['~cwp_work_0'],
                "%s lost its level-0 work" % label)

    def test_partial_result_is_independent_of_lookup_order(self):
        """With cwp_partial on, whether a shared parent node had already been
        renamed depended on the order the async lookups returned in, so the
        same release tagged differently from run to run -- d1t6 came out at
        part_levels 2 under fifo and 3 under lifo, and which tracks were
        stranded moved around too. Same cause as
        test_every_track_keeps_its_work_metadata."""
        baseline = None
        orders = ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]
        for order in orders:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(tracks[label].metadata['~cwp_work_top']),
                        self.mod.str_to_list(tracks[label].metadata['~cwp_work_0']),
                        tracks[label].metadata['~cwp_part_levels'])
                for label, _d, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
                # sanity: the baseline is the CORRECT answer, not a stable
                # wrong one -- every track tagged, under the one ballet
                for label in baseline:
                    self.assertEqual(baseline[label][0], ["Daphnis et Chloé"],
                                     "%s top work" % label)
            else:
                self.assertEqual(result, baseline,
                                 "drain order %r changed the tags" % order)


class FirebirdPartialTopWorkIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release 0b54b210 (Stravinsky: The Song of the
    Nightingale / The Firebird Suite / The Rite of Spring).

    Tracks 1-7 are the 1919 Firebird suite: each recording performs one
    movement, and every movement is 'parts' of the suite work 1a00a184. Track 5
    alone carries a SECOND performance relation, marked 'partial', straight to
    the full ballet 2fbaaf2d -- which is a root in its own right, not an
    ancestor of the suite (the suite is only 'based on' it).

    So track 5 is the one track on the album whose two works sit under two
    different top works. Reported symptom: track 5 does not come out under the
    Firebird top work its six siblings get."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "firebird")
    _REL = "0b54b210-bae2-4da0-bd05-1d117bf674f1"
    _SUITE = "1a00a184-8e67-4b01-a496-81b1b6df9a88"   # L'Oiseau de feu, suite de 1919
    _BALLET = "2fbaaf2d-deff-46fb-ac15-ac76e9e56eca"  # L'Oiseau de feu (full ballet)
    _NIGHTINGALE = "a79fb213-970d-428b-8982-cbde96ba060e"
    _RITE = "54dacada-b00c-34b6-bbbb-e89855c7219f"
    _FIREBIRD_TRACKS = ["t%d" % n for n in range(1, 8)]
    # (label, track, recording id, work id(s))
    _TRACKS = [
        ('t1', 1, '637eb54f-dac8-4f41-a5a5-b1df7a8fa0e6',
         'a5e780df-3693-40a5-9dc0-0623033a8ae0'),
        ('t2', 2, 'f9c2a258-bed5-405b-948d-6bc312d1ea66',
         '123a4c09-3bde-4624-ae15-ef55ee7f95e1'),
        ('t3', 3, 'cc8cb52e-6e8b-4543-8b71-06a97ae656c2',
         'a594bc7b-2436-4b00-815e-13ab2e02c92e'),
        ('t4', 4, '5c863af9-7be0-48d7-9cc5-94f6d878c0a5',
         'fe91c939-f74f-42b8-908c-e212d26b705a'),
        ('t5', 5, '036a332c-4e01-4e74-8528-456060d37182',
         ['a06c71e7-e293-4206-a40b-a4cf89b34cb0',
          '2fbaaf2d-deff-46fb-ac15-ac76e9e56eca']),
        ('t6', 6, 'f3e9c4b3-5d23-4e34-b34d-ee796bf08d47',
         '223dae8d-badc-42a8-a94e-86cf9adf9338'),
        ('t7', 7, '63fb30a8-2a3f-475f-8882-d31b1292e914',
         '0eb658ac-cab3-4008-a4fa-ab0565f99dc8'),
        ('t8', 8, '56214ec7-52d6-4384-bee1-a315377417e6',
         '3e68f8ca-88bd-4c08-b50f-5bc24469076d'),
        ('t9', 9, 'ba1c4b4b-65c1-47be-819a-d5fdbdf6e1b9',
         '548717ae-4e49-48d8-98d5-a06d2a2fc125'),
        ('t10', 10, '36b60507-8ffa-4e0b-ad97-a59350c4ba7f',
         'b7e718be-e840-4cfe-9aed-698c666e6eec'),
        ('t11', 11, '7473cf59-445a-4b60-853a-7c4c33a968d2',
         '8a38160f-0c08-4c69-8fd1-261750a23cf1'),
        ('t12', 12, '38c81495-2a16-4308-a8c2-ee25607887b8',
         '40fd0ab1-ccc7-347a-91ff-6b5d9fa40cd4'),
        ('t13', 13, 'fbbc1256-aaa7-40b5-b256-b105d3c4d63e',
         '29e4d499-4ec2-3879-9839-b57fd977298d'),
        ('t14', 14, '744a9ae4-d591-4581-8254-e52258245cc6',
         '1ff0d8a2-5a40-3f09-87f8-66331f1b044b'),
        ('t15', 15, '5e3dbd6e-eb57-4ea5-b553-2527ac13c03a',
         '4090b576-d60f-3e9b-a587-dd79648e0ec7'),
        ('t16', 16, 'c8d02b19-ff2f-451c-99f7-42c975a31a21',
         'a37aedd7-08df-35c6-9a97-29cb61192d45'),
        ('t17', 17, '52cf34f5-63d9-4c25-ba3a-c8de256eae86',
         'c8700d76-b8b8-37eb-80f2-e3e9ee24dc30'),
        ('t18', 18, '15f5b52c-044c-445e-9f45-73ba19e7385e',
         '87aea44d-eb59-3656-ad88-9e060d28dab9'),
        ('t19', 19, 'eb0c7db9-c5d4-4480-bc1f-583105cdc1f3',
         '48e140f4-d75f-3990-8c5b-f9c07ad11670'),
        ('t20', 20, '52e6f664-6d8e-4490-9a6c-f486535cc00a',
         '4608f8ba-7df2-3f36-b0d6-5999fefb7aca'),
        ('t21', 21, 'afb82133-253a-4d97-adef-74432b23958f',
         'dc312a3c-c83f-348e-95b1-7617fcc62541'),
        ('t22', 22, '883e4315-47cd-4cea-917f-09f7639a2d4f',
         'eedc0a88-6b55-3ed2-aacf-f93f7cb26272'),
        ('t23', 23, '26953305-17f2-462e-9893-5c3bf3121835',
         'e6e27966-53bf-39c1-b1a2-ddde5ac1e4aa'),
        ('t24', 24, 'c6ced445-a58f-44ae-b4ef-8376d15e5727',
         'a2d06e4c-9988-3aca-a244-23bafb74a99c'),
        ('t25', 25, '41f66d4e-8fda-44d9-a93f-cf51d36aaec9',
         '75ab293f-86d2-324e-85ed-afbc873b74b3'),
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
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _run_full_album(self, drain="fifo", partial=True):
        """Drive all 25 tracks through the real Picard flow."""
        import random
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
            "cwp_partial": partial, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        class _M(dict):
            def __getitem__(self, k):
                return self.get(k, '')

            def getall(self, k):
                v = self.get(k)
                return [] if v is None else (v if isinstance(v, list) else [v])

        tracks = {}
        for label, track, rec_id, work_id in self._TRACKS:
            tm = _M(musicbrainz_albumid=self._REL,
                    musicbrainz_recordingid=rec_id,
                    musicbrainz_workid=work_id,
                    album="The Song of the Nightingale / The Firebird Suite"
                          " / The Rite of Spring",
                    title=label, tracknumber=str(track), discnumber="1")
            tm['~ce_options'] = repr(opts)
            t = Mock(name=label)
            t.metadata = tm
            t._id = label
            t.__hash__ = lambda self: hash(self._id)
            t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        self._pl = pl
        self._album = album
        return tracks

    def test_track_5_keeps_the_firebird_top_work(self):
        """The reported bug. Track 5's extra 'partial' relation to the whole
        ballet must not cost it the top work its six siblings share."""
        tracks = self._run_full_album()
        tops = self.mod.str_to_list(tracks['t5'].metadata['~cwp_workid_top'])
        self.assertIn(self._SUITE, tops,
                      "track 5 lost the Firebird suite top work; got %r"
                      % (tracks['t5'].metadata['~cwp_work_top'],))

    def test_all_firebird_tracks_share_one_top_work(self):
        """Tracks 1-7 are one work: they must agree on the top work under
        every lookup order."""
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]:
            tracks = self._run_full_album(drain=order)
            for label in self._FIREBIRD_TRACKS:
                tm = tracks[label].metadata
                self.assertIn(
                    self._SUITE,
                    self.mod.str_to_list(tm['~cwp_workid_top']),
                    "%s (order %s) top work is %r"
                    % (label, order, tm['~cwp_work_top']))

    def test_every_track_keeps_its_work_metadata(self):
        """No track may be stranded by the extra relation on track 5."""
        tracks = self._run_full_album()
        for label, _t, _r, _w in self._TRACKS:
            self.assertTrue(
                tracks[label].metadata['~cwp_workid_top'],
                "%s lost its work metadata" % label)
            self.assertTrue(
                tracks[label].metadata['~cwp_work_0'],
                "%s lost its level-0 work" % label)

    def test_the_other_two_works_are_unaffected(self):
        """The Nightingale (8-11) and the Rite (12-25) are ordinary
        hierarchies on the same album; they pin the rest of the release."""
        tracks = self._run_full_album()
        for label in ["t%d" % n for n in range(8, 12)]:
            self.assertEqual(
                self.mod.str_to_list(
                    tracks[label].metadata['~cwp_workid_top']),
                [self._NIGHTINGALE], "%s top work" % label)
        for label in ["t%d" % n for n in range(12, 26)]:
            self.assertEqual(
                self.mod.str_to_list(
                    tracks[label].metadata['~cwp_workid_top']),
                [self._RITE], "%s top work" % label)

    def test_result_is_independent_of_lookup_order(self):
        """Whatever the album resolves to, it must resolve to the same thing
        every time -- the async work lookups return in network order."""
        baseline = None
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_top']),
                        self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_0']),
                        tracks[label].metadata['~cwp_part_levels'])
                for label, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
            else:
                self.assertEqual(result, baseline,
                                 "drain order %r changed the tags" % order)


class SwanLakeFullBalletTwoVersionsIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release a1a9e501 (Tchaikovsky: Swan Lake, complete,
    2 discs / 43 tracks).

    MusicBrainz carries the ballet twice -- the original op. 20 (11f48c5e) and
    the 1895 R. Drigo edition (13867eb1) -- and most of this release's movement
    works are 'parts' of BOTH: once through the Drigo act works and once
    through the original's act/number works. Only 9 tracks reach a single root
    (8 Drigo, 1 original).

    Reported symptom: the album comes out with two top works, so a track is
    tagged with both versions of Swan Lake at once."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures",
                           "swanlake_full")
    _REL = "a1a9e501-a7df-45b4-9879-ddd9661d0f65"
    _ORIGINAL = "11f48c5e-5ee9-4646-9826-fb7c2fccce7f"   # Swan Lake, op. 20
    _DRIGO = "13867eb1-42c2-45ed-97d9-e1cc85b006fd"      # 1895 R. Drigo Edition
    # Tracks whose work reaches only one root.
    _ONLY_DRIGO = ('d1t18', 'd2t10', 'd2t11', 'd2t12', 'd2t16', 'd2t18',
                   'd2t19', 'd2t20')
    _ONLY_ORIGINAL = ('d2t2',)
    # (label, disc, track, recording id, work id)
    _TRACKS = [
        ('d1t1', 1, 1, 'eb2b8067-0f42-4723-bfd4-2ad8b1f47e3a',
         '58907391-15e4-3095-90a3-743583eb7039'),
        ('d1t2', 1, 2, '81795ffe-3f47-4506-a501-ab14c51fccf3',
         'd15ce760-99c4-36bc-b2d7-96a6e24b8e11'),
        ('d1t3', 1, 3, 'd8bddaa8-ec9e-4bae-b830-69016bb29ecc',
         'f32adc94-5198-347b-b275-08a6db82f686'),
        ('d1t4', 1, 4, 'e67d38c7-e053-4ed9-afdc-f60e5dfeda61',
         'bafdc758-3521-3e27-952f-f724011b5f73'),
        ('d1t5', 1, 5, '807e3d84-76e5-4c6d-895a-bf48286b8ae3',
         '92d6dc8c-ce35-3933-bd87-ca224b70315d'),
        ('d1t6', 1, 6, 'dd7549c9-e4af-4d1c-b7c3-587203a576f5',
         'c9b2a368-1b94-3c7d-a116-53b7998e0fd4'),
        ('d1t7', 1, 7, '016578bb-ddd8-4ccb-9a28-20b2f32e15b9',
         '19e52375-1eaf-3a11-bbe6-ad8ff4543ed2'),
        ('d1t8', 1, 8, '2886683d-2ac4-4a5c-bb30-9d402b2ba138',
         '4fad32b7-8d81-3097-8947-5115789cf346'),
        ('d1t9', 1, 9, 'f581bd74-7e7f-4ac0-b9e1-4810bd6da827',
         '538bc728-d945-334e-863d-85ac78de6e7b'),
        ('d1t10', 1, 10, '22bd7f25-ea65-4a34-89f6-5e098a9bfa9a',
         '6da5000b-656d-3827-a2c5-adbeed72becb'),
        ('d1t11', 1, 11, 'e27b8774-9ff5-4fd3-93f7-452be2cb706e',
         '0607862a-8ec9-35a1-a9cb-6f013b96bc00'),
        ('d1t12', 1, 12, 'ace57164-e1d3-4e56-9ee4-584a70da05f4',
         '96ffa250-7241-315c-9816-1019e2a32416'),
        ('d1t13', 1, 13, '29f7a2f6-011d-4631-82a5-83a30b4d5d03',
         'aea64fe4-1a6e-38d1-97b7-99a220634234'),
        ('d1t14', 1, 14, '619d9e85-35fc-4415-a78a-3c4d9bd837b6',
         'efcc7513-7303-31f9-a1ca-6fec650cb801'),
        ('d1t15', 1, 15, '3c8cd4ca-9bd1-4de9-812a-de375396b44d',
         'cc2e2a57-098a-320a-9644-1457eb040ec7'),
        ('d1t16', 1, 16, 'cccf205f-b9ac-488f-8c16-84cfa1953764',
         '6d7ee040-e7e9-3500-b63f-3584cc2e5cd1'),
        ('d1t17', 1, 17, 'bec54809-b50d-4d25-a6e1-776497e0751d',
         '89c4f004-98b2-3334-ab32-ca9409e79ad9'),
        ('d1t18', 1, 18, '02d31e8c-33d7-412e-8c67-12e0903dc901',
         '34103e67-a6a1-4d22-955c-9ac4220f854e'),
        ('d1t19', 1, 19, 'e70c2550-0f43-4199-a244-5d3c3d9d944d',
         '09a40707-a00f-3866-9b80-81a16928465f'),
        ('d1t20', 1, 20, 'e6941636-d44c-46d3-a2b4-6aed3687f032',
         '7cf29f44-2874-4bd6-bfd6-66564e331d23'),
        ('d1t21', 1, 21, '5f4e5c42-6eaf-4e05-ae06-63a954baa819',
         'c490180d-bae6-3336-84fa-59d89fc46de9'),
        ('d1t22', 1, 22, '2cec599b-bbf1-4daa-898a-e52f249eb8a9',
         '9e7a1e2e-5f87-43c0-80e4-01567fa86b56'),
        ('d1t23', 1, 23, '5e885ca3-bef0-4f66-a39d-d67fb8320bd4',
         'bd56f23f-6ddd-4d24-ba8c-3f20eaf066d1'),
        ('d2t1', 2, 1, 'c4edfdd3-8d25-4b2d-a415-567945411e09',
         '0471148d-3968-40f3-a7b8-3bdb1e4ae176'),
        ('d2t2', 2, 2, 'b650402e-6c50-41ac-a27b-f98e06c454d0',
         '5b130c88-2b07-4f3a-ab04-705eb1e9d13e'),
        ('d2t3', 2, 3, '1ef60cbb-ff35-494d-a9b9-5f9a86e78dbd',
         '17a59c78-99f0-47b6-859f-2e854dcd30aa'),
        ('d2t4', 2, 4, '28e25d79-d8ed-460e-9d48-fed8c49b1e24',
         'c6f93641-f036-4f18-a55d-dcbf183c68af'),
        ('d2t5', 2, 5, '4b3d823e-30ad-4080-8899-9450ce939cd9',
         'c148e7d2-269b-4faf-9c93-82b79c5ef54c'),
        ('d2t6', 2, 6, '37b471e7-a5fd-4ae0-b0c7-79f9bc465743',
         '30f2e247-f345-4f3b-a7cc-09c09fc53e9b'),
        ('d2t7', 2, 7, '1b406f18-0779-4140-a105-8be90a2c1dba',
         '12818a2e-048a-475a-a279-66d8f90f4193'),
        ('d2t8', 2, 8, '6221b79a-2c87-404f-9ee4-eb797dc90fe5',
         '1a972d5e-ee15-4108-b16c-5585e54c1540'),
        ('d2t9', 2, 9, 'db15a9e7-eca4-4eb5-be04-44fe2a049ef4',
         'de8c7fd6-1b3c-3fe5-89de-143cffc8bed2'),
        ('d2t10', 2, 10, '274c546d-fdee-4305-b5a4-bb4443991635',
         '5b0ad9f9-22d0-4181-a45e-4108511687bf'),
        ('d2t11', 2, 11, 'd31c6098-2489-4815-b9c9-608cb93d2daf',
         '1f33d81d-186d-4cbd-8530-d98f3eaab1fd'),
        ('d2t12', 2, 12, 'e41a9694-f64f-42fa-9db4-8f838b09ebed',
         '200e0f8f-77f3-4c7a-8da3-f7fcc1397ff7'),
        ('d2t13', 2, 13, 'd35fee9e-8794-4528-8cd7-b4aee947eb3c',
         '6f11edd1-b6be-3895-a2d8-40f501efb5a2'),
        ('d2t14', 2, 14, 'c941a3c9-f9fa-4376-ab2d-7243c424f365',
         'f12bea2c-7c34-47f9-9f68-66560e66a645'),
        ('d2t15', 2, 15, 'a8cef37c-b41e-47fe-b04e-cadd82e1a879',
         'c370363b-3d2b-41e4-8541-b752c49a6f07'),
        ('d2t16', 2, 16, 'a68ef316-8454-489a-bad6-bc8a7cd2e96e',
         '27e185cb-3078-465f-8ce1-cdfeda39aa71'),
        ('d2t17', 2, 17, '3e188351-6197-462d-9a4a-a28da8ca48ee',
         '8b81b827-2554-4f8f-b62a-0823bf4b57f4'),
        ('d2t18', 2, 18, '706709c6-5d60-4849-b02f-7824e074d4d5',
         '96e46066-78b2-4d5f-a10f-dac2b4dc2b58'),
        ('d2t19', 2, 19, '9aebc44f-30bc-4c10-bc4e-83940f04ac6f',
         '894c67c6-e999-4b8a-8913-58f627b7bcdd'),
        ('d2t20', 2, 20, 'd8180f9e-f81d-450a-b94a-fa086eecd57f',
         '3d749164-4223-4526-b0ea-a65bc2235038'),
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
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": False, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _run_full_album(self, drain="fifo", partial=True, arrangements=True):
        """Drive all 43 tracks through the real Picard flow."""
        import random
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
            "cwp_partial": partial, "cwp_arrangements": arrangements,
            "cwp_medley": False, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        class _M(dict):
            def __getitem__(self, k):
                return self.get(k, '')

            def getall(self, k):
                v = self.get(k)
                return [] if v is None else (v if isinstance(v, list) else [v])

        tracks = {}
        for label, disc, track, rec_id, work_id in self._TRACKS:
            tm = _M(musicbrainz_albumid=self._REL,
                    musicbrainz_recordingid=rec_id,
                    musicbrainz_workid=work_id, album="Swan Lake",
                    title=label, tracknumber=str(track), discnumber=str(disc))
            tm['~ce_options'] = repr(opts)
            t = Mock(name=label)
            t.metadata = tm
            t._id = label
            t.__hash__ = lambda self: hash(self._id)
            t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        self._pl = pl
        self._album = album
        return tracks

    def test_no_track_is_tagged_with_both_versions(self):
        """The reported bug: a track must name ONE Swan Lake, not both the
        original and the Drigo edition."""
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            tm = tracks[label].metadata
            tops = self.mod.str_to_list(tm['~cwp_workid_top'])
            self.assertEqual(
                len(tops), 1,
                "%s has %d top works: %r" % (label, len(tops),
                                             tm['~cwp_work_top']))

    def test_whole_album_agrees_on_one_version(self):
        """43 tracks of one ballet: the album must settle on a single top."""
        tracks = self._run_full_album()
        tops = {tuple(self.mod.str_to_list(
                    tracks[label].metadata['~cwp_workid_top']))
                for label, _d, _t, _r, _w in self._TRACKS}
        self.assertEqual(len(tops), 1,
                         "album split across tops: %r" % (tops,))

    def test_unambiguous_tracks_keep_their_own_root(self):
        """A track whose work reaches only one root must be tagged with that
        root -- it is not a candidate for any other."""
        tracks = self._run_full_album()
        for label in self._ONLY_DRIGO:
            self.assertIn(
                self._DRIGO,
                self.mod.str_to_list(tracks[label].metadata['~cwp_workid_top']),
                "%s should be under the Drigo edition" % label)

    def test_every_track_keeps_its_work_metadata(self):
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            self.assertTrue(tracks[label].metadata['~cwp_workid_top'],
                            "%s lost its work metadata" % label)
            self.assertTrue(tracks[label].metadata['~cwp_work_0'],
                            "%s lost its level-0 work" % label)

    def test_result_is_independent_of_lookup_order(self):
        baseline = None
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_top']),
                        self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_0']),
                        tracks[label].metadata['~cwp_part_levels'])
                for label, _d, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
            else:
                self.assertEqual(result, baseline,
                                 "drain order %r changed the tags" % order)

class NutcrackerSuiteOverlapIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release b2f4b6e5 (Tchaikovsky: The Nutcracker,
    complete, 2 discs / 24 tracks).

    23 of the 24 tracks link to exactly one work, all under the ballet
    "Щелкунчик, op. 71" (f3281e81). Disc 1 track 1 (Miniature Overture) is
    recorded against TWO works: the ballet's own Увертюра (91421046) and the
    first movement of the concert suite "The Nutcracker (suite from the
    ballet), op. 71a" (fce0c96b), whose top is a different work (fd337fdc).

    Reported symptom: disc 1 track 1 does not share the album's top work -- it
    comes out under the suite while every other track is under the ballet."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "nutcracker")
    _REL = "b2f4b6e5-e114-4e6e-ad18-879976885304"
    _BALLET = "f3281e81-eea2-409f-88b8-9e1e1de5ca10"   # Щелкунчик, op. 71
    _SUITE = "fd337fdc-a511-4830-8ee7-09d4ada32a54"    # ... suite, op. 71a
    _BALLET_NAME = "Щелкунчик, op. 71"
    _SUITE_NAME = "The Nutcracker (suite from the ballet), op. 71a"
    # (label, disc, track, recording id, work id(s))
    _TRACKS = [
        # Miniature Overture: one recording, two works -- the ballet's
        # overture and the suite's first movement.
        ('d1t1', 1, 1, '3e557fd6-e178-4736-9250-9976ce58e180',
         ['91421046-15ce-30c9-9d70-8c5669ddbeee',
          'fce0c96b-42a9-4c41-a455-d5dc017bcb44']),
        ('d1t2', 1, 2, '9c32ad42-fdf0-4482-904a-3e32e9309428',
         'f1cd9dc7-625a-36f5-a0fe-ac645d5e8c1c'),
        ('d1t3', 1, 3, '2a1e90eb-7153-4e1a-9a8a-2849d6f29706',
         'a93104a5-fd64-3f1d-9331-d5405781a5e7'),
        ('d1t4', 1, 4, '8ca2bb09-5ff3-4f22-992c-46307e45bdbc',
         'dd9d1429-526d-31e0-a933-b72f4d39216a'),
        ('d1t5', 1, 5, '44ac0eae-0f46-41dc-a245-cf6af2e7b58a',
         'ff037004-5dc9-3984-b4db-f6fac19d431d'),
        ('d1t6', 1, 6, 'a8a29a4f-4132-4070-aec9-8e5233de4bbe',
         '7a231a49-debd-389d-a6c3-e921622598cb'),
        ('d1t7', 1, 7, 'b76fbd03-95bd-474c-80e0-edb21a57f327',
         'd56cb3fc-77a2-385b-962b-6e91d915dfd7'),
        ('d1t8', 1, 8, '43df2df4-7b33-48f8-a29a-8474629d0d85',
         '9f640e6d-5211-3ad6-8a7f-36136fc928ff'),
        ('d1t9', 1, 9, '135866ed-8b21-4f8f-af23-c8b49fc320b2',
         '2f4888c4-6e13-392d-8ad8-2294f1d517a9'),
        ('d1t10', 1, 10, '1f0a558e-2cd1-4ba6-8e37-97277c3a0f58',
         '8d44d4c1-5a55-3273-bff0-86cbde10f6c4'),
        ('d2t1', 2, 1, '564423e7-08e9-4848-a69f-9ef698e52fbb',
         '74c29f32-3051-3236-85ac-0b81a158fb7e'),
        ('d2t2', 2, 2, 'cc1a3129-7559-4280-bbe9-d203b0d64e2a',
         '44e2d780-78cc-3552-ba7c-d44e9dd6d2b7'),
        ('d2t3', 2, 3, '511ca038-d40d-4044-8fce-967bf6db502e',
         'a7d61779-a4f3-3e1e-8a4f-a1a4916e326a'),
        ('d2t4', 2, 4, '01470b15-13ed-43a8-898a-02fb146758cf',
         '9a717451-ef0d-326c-88c7-a7d61f9ebddd'),
        ('d2t5', 2, 5, '9799f4d0-c828-4415-80cc-e67163be34b8',
         '98b402ca-1bd0-3de4-8b77-6dd85ba0191e'),
        ('d2t6', 2, 6, '71d00138-9b77-48af-8586-8a5ea97e6e7c',
         '2c16255f-a245-3dde-8860-d37424a639db'),
        ('d2t7', 2, 7, 'e5197389-f0ef-4378-88fd-c5d5651d0973',
         '5f51ef98-34a6-3b7b-bede-32b4cdd33eed'),
        ('d2t8', 2, 8, '9c90db12-1bc7-4c2f-abdc-6af4c0be48c5',
         '4617c13c-6597-36b0-969e-ced9603e6889'),
        ('d2t9', 2, 9, 'a29510b3-16f7-4589-848c-6073b5729efb',
         'ba28e7e2-862e-3438-9faa-13ebb6e9030c'),
        ('d2t10', 2, 10, 'a2ba4773-4a6e-464b-b0ee-2f503a39828a',
         'fbffe214-835e-3930-8349-d42ff93f2648'),
        ('d2t11', 2, 11, '9dd0bd6f-8857-4005-b3c1-a4cdce55e7dc',
         '0dc80be8-dac8-3f33-a22b-464c64f792b1'),
        ('d2t12', 2, 12, '592aba8c-2a44-4ab2-a8a4-9e31fb704e20',
         '5fd9c9dd-9e0d-31cf-9861-58b7abd1d12f'),
        ('d2t13', 2, 13, '8fec78c3-99ce-42cc-b707-6c6f9dc307e8',
         '6a3d8f7a-c492-3614-ab90-f45a9a5ed2da'),
        ('d2t14', 2, 14, 'a1de0149-8f01-4f7e-b3f4-f21d8df3d34f',
         '9dc735d6-9824-36fa-9881-9b34b853ff2b'),
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
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": True, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _run_full_album(self, drain="fifo", partial=True, arrangements=True):
        """Drive all 24 tracks through the real Picard flow."""
        import random
        from unittest.mock import Mock
        mod = self.mod
        pl = mod.PartLevels()
        # Movement numbering happens inside the real extend_metadata /
        # publish_metadata pair, so a test that asserts on movementnumber /
        # movementtotal has to let them run (_STUB_PUBLISH = False).
        if getattr(self, "_STUB_PUBLISH", True):
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
            "cwp_partial": partial, "cwp_arrangements": arrangements,
            "cwp_medley": True, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        # Per-test option overrides (used to replay a user's real Picard
        # config, where e.g. cwp_medley is on).
        opts.update(getattr(self, "_extra_opts", {}))
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        from picard.metadata import Metadata

        tracks = {}
        for label, disc, track, rec_id, work_id in self._TRACKS:
            # Picard's real Metadata (not a dict mock): it joins multi-values
            # into a string on __getitem__, which the publish/extend path
            # relies on.
            tm = Metadata()
            tm['musicbrainz_albumid'] = self._REL
            tm['musicbrainz_recordingid'] = rec_id
            tm['musicbrainz_workid'] = work_id
            tm['album'] = "The Nutcracker"
            tm['title'] = label
            tm['tracknumber'] = str(track)
            tm['discnumber'] = str(disc)
            tm['~ce_options'] = repr(opts)
            t = Mock(name=label)
            t.metadata = tm
            t._id = label
            t.__hash__ = lambda self: hash(self._id)
            t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        self._pl = pl
        self._album = album
        return tracks

    def test_overture_shares_the_album_top_work(self):
        """The reported bug: disc 1 track 1 must be under the ballet, like the
        other 23 tracks, not under the concert suite."""
        tracks = self._run_full_album()
        self.assertEqual(
            self.mod.str_to_list(tracks['d1t1'].metadata['~cwp_workid_top']),
            [self._BALLET],
            "d1t1 top = %r (%s)" % (tracks['d1t1'].metadata['~cwp_workid_top'],
                                    tracks['d1t1'].metadata['~cwp_work_top']))

    def test_whole_album_agrees_on_one_top(self):
        tracks = self._run_full_album()
        tops = {tuple(self.mod.str_to_list(
                    tracks[label].metadata['~cwp_workid_top']))
                for label, _d, _t, _r, _w in self._TRACKS}
        self.assertEqual(len(tops), 1,
                         "album split across tops: %r" % (tops,))

    def test_top_work_names_the_ballet_only(self):
        """The user-visible half of the bug. The overture's suite parent must
        not be fused into the album's top work: ~cwp_work_top (the source of
        the `top_work` tag) has to name the ballet alone, on every track --
        never 'Щелкунчик, op. 71; The Nutcracker (suite from the ballet)'.

        This is a NAME-level failure only: ~cwp_workid_top stayed a single id
        throughout, so an id-only assertion does not see it."""
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(20)]:
            tracks = self._run_full_album(drain=order)
            for label, _d, _t, _r, _w in self._TRACKS:
                names = self.mod.str_to_list(
                    tracks[label].metadata['~cwp_work_top'])
                self.assertEqual(
                    names, [self._BALLET_NAME],
                    "drain %r: %s top_work = %r" % (order, label, names))

    def test_every_track_keeps_its_work_metadata(self):
        tracks = self._run_full_album()
        for label, _d, _t, _r, _w in self._TRACKS:
            self.assertTrue(tracks[label].metadata['~cwp_workid_top'],
                            "%s lost its work metadata" % label)
            self.assertTrue(tracks[label].metadata['~cwp_work_0'],
                            "%s lost its level-0 work" % label)

    def test_every_track_is_counted_as_a_movement(self):
        """24 tracks of one ballet: movements must run 1..24 of 24, with the
        overture (d1t1) included rather than stranded in a group of its own."""
        self._STUB_PUBLISH = False
        tracks = self._run_full_album()
        for n, (label, _d, _t, _r, _w) in enumerate(self._TRACKS, start=1):
            tm = tracks[label].metadata
            self.assertEqual(
                (tm['movementnumber'], tm['movementtotal']), (str(n), '24'),
                "%s: movement %r of %r" % (label, tm['movementnumber'],
                                           tm['movementtotal']))

    def test_result_is_independent_of_lookup_order(self):
        baseline = None
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_top']),
                        self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_0']),
                        tracks[label].metadata['~cwp_part_levels'])
                for label, _d, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
            else:
                self.assertEqual(result, baseline,
                                 "drain order %r changed the tags" % order)


class RequiemCatchAllEditionIntegrationTestCase(ClassicalExtrasTestCase):
    """End-to-end guard for release 63fb5437 (Mozart: Requiem, Bernstein,
    1 disc / 14 tracks).

    The same shape as the Nutcracker (b2f4b6e5), with the twist that the two
    tops are character-identical. 13 of the 14 tracks link to exactly one work,
    all under "Requiem in D minor, K. 626" [Beyer/Kunzelmann Edition]
    (d0be6882). Track 1 is recorded against TWO works: the Beyer/Kunzelmann
    Introitus (7b8867e5) and the Introitus of a SECOND K. 626 work, the
    "catch-all for unknown editions" (5ba9868e), which is its own top.

    Both tops are titled "Requiem in D minor, K. 626" and differ only by
    disambiguation, so a split here is invisible in ~cwp_work_top -- it has to
    be asserted on ~cwp_workid_top."""

    _FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "requiem")
    _REL = "63fb5437-9b7d-440c-aad2-0635bfbacee6"
    _BEYER = "d0be6882-700f-48c9-af49-1f3d42627de5"     # Beyer/Kunzelmann Ed.
    _CATCHALL = "5ba9868e-f053-4520-8957-6a9fedb2455a"  # unknown editions
    _TOP_NAME = "Requiem in D minor, K. 626"            # BOTH of them
    # (label, track, recording id, work id(s))
    _TRACKS = [
        # Introitus: one recording, two works -- the Beyer/Kunzelmann movement
        # and the catch-all edition's movement. The bridge between the tops.
        ('t1', 1, '182530c7-d72d-4504-bfbb-d4a8cdcd4b78',
         ['7b8867e5-b146-482f-bfdd-9ee0e4471b37',
          'a43e535d-671d-406b-9b3f-b80559242bf5']),
        ('t2', 2, 'f879e18d-fba9-41af-861b-761eb8011902',
         'e9a58e22-5092-49f0-9f3f-9191fb392a81'),
        ('t3', 3, '224f7281-ab5c-4708-8bd6-bd83fe0c8d04',
         '6198f617-8ea4-41d9-ba4f-742512cf0123'),
        ('t4', 4, '8f65f257-fdbd-4ac1-96aa-b263b25b523a',
         '60c4c27c-52c2-4af7-ba57-82e61c091f66'),
        ('t5', 5, '9b030c1b-e068-4697-ae6e-94615c0fc319',
         '9c18c926-c6b3-4e06-b50f-fd453bf18e8c'),
        ('t6', 6, '2ce37150-a297-42f3-918c-f4e592e99a68',
         '50827de7-974d-4f0a-89aa-2151b071c0df'),
        ('t7', 7, 'f88fbaf3-ab29-4acc-9f4a-6d7f1e848e9a',
         '1766cf64-5b60-4a4f-bec6-6195247c83ce'),
        ('t8', 8, 'b8b5a68c-f60c-4c49-bc0d-e925b5a08d91',
         '20b30605-16fe-4775-911d-cb4ba3a86422'),
        ('t9', 9, '5099bd66-220c-4e22-a9e4-71c3003a9ae0',
         'f20850fa-1208-4cb3-a69d-5b02558198b5'),
        ('t10', 10, 'e811cbc0-48de-43d3-8eb5-ce663fb66c1e',
         'af70cc9a-d670-4722-8447-1bc85d6d88e6'),
        ('t11', 11, '97b4a245-1865-43c6-94e7-ba21243ae180',
         '49954224-a4b6-48b7-9f38-c719cf823ec1'),
        ('t12', 12, '6013dfe3-719c-46af-aee2-98b096a48f00',
         'aaa7e36a-b909-49d9-ad2d-9033a9d3be54'),
        ('t13', 13, '86ce292c-681d-414d-945a-c5c20c6da3af',
         '68d21c1a-03cb-4f33-bf71-022ad990883b'),
        ('t14', 14, 'c9131c55-ad7c-4229-b161-c41242148241',
         '9fa1ab5f-f90c-4727-bcc9-c64aad8cd1a8'),
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
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": True, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    def _run_full_album(self, drain="fifo", partial=True, arrangements=True):
        """Drive all 14 tracks through the real Picard flow."""
        import random
        from unittest.mock import Mock
        mod = self.mod
        pl = mod.PartLevels()
        # Movement numbering happens inside the real extend_metadata /
        # publish_metadata pair, so a test that asserts on movementnumber /
        # movementtotal has to let them run (_STUB_PUBLISH = False).
        if getattr(self, "_STUB_PUBLISH", True):
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
            "cwp_partial": partial, "cwp_arrangements": arrangements,
            "cwp_medley": True, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts.update(getattr(self, "_extra_opts", {}))
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        from picard.metadata import Metadata

        tracks = {}
        for label, track, rec_id, work_id in self._TRACKS:
            # Picard's real Metadata (not a dict mock): it joins multi-values
            # into a string on __getitem__, which the publish/extend path
            # relies on.
            tm = Metadata()
            tm['musicbrainz_albumid'] = self._REL
            tm['musicbrainz_recordingid'] = rec_id
            tm['musicbrainz_workid'] = work_id
            tm['album'] = "Requiem"
            tm['title'] = label
            tm['tracknumber'] = str(track)
            tm['discnumber'] = "1"
            tm['~ce_options'] = repr(opts)
            t = Mock(name=label)
            t.metadata = tm
            t._id = label
            t.__hash__ = lambda self: hash(self._id)
            t.__eq__ = lambda self, other: getattr(other, "_id", None) == self._id
            tracks[label] = t
            album._new_tracks.append(t)
            node = {'recording': self._load("rec_%s.json" % label)}
            pl.add_work_info(album, t.metadata, node, {})

        rng = random.Random(drain)
        while pending:
            if drain == "fifo":
                i = 0
            elif drain == "lifo":
                i = len(pending) - 1
            else:
                i = rng.randrange(len(pending))
            cb, resp = pending.pop(i)
            cb(resp, None, None)
        self._pl = pl
        self._album = album
        return tracks

    def test_introitus_shares_the_album_top_work(self):
        """The reported bug: track 1 must sit under the same K. 626 as the
        other 13, not under the catch-all edition."""
        tracks = self._run_full_album()
        self.assertEqual(
            self.mod.str_to_list(tracks['t1'].metadata['~cwp_workid_top']),
            [self._BEYER],
            "t1 top = %r" % (tracks['t1'].metadata['~cwp_workid_top'],))

    def test_whole_album_agrees_on_one_top_id(self):
        """Both tops share a title, so this must be asserted on the ID."""
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(20)]:
            tracks = self._run_full_album(drain=order)
            tops = {tuple(self.mod.str_to_list(
                        tracks[label].metadata['~cwp_workid_top']))
                    for label, _t, _r, _w in self._TRACKS}
            self.assertEqual(
                tops, {(self._BEYER,)},
                "drain %r: album tops = %r" % (order, tops))

    def test_every_track_keeps_its_work_metadata(self):
        tracks = self._run_full_album()
        for label, _t, _r, _w in self._TRACKS:
            self.assertTrue(tracks[label].metadata['~cwp_workid_top'],
                            "%s lost its work metadata" % label)
            self.assertTrue(tracks[label].metadata['~cwp_work_0'],
                            "%s lost its level-0 work" % label)

    def test_every_track_is_counted_as_a_movement(self):
        """The reported symptom: track 1 is not counted as a movement.

        When track 1 is stranded under a top of its own, that top's
        movement-total is 1, publish_metadata's ``movementtotal > 1`` guard
        fails and the track gets no movementnumber/movementtotal at all --
        while the remaining 13 are renumbered 1..13 as their own group."""
        self._STUB_PUBLISH = False
        tracks = self._run_full_album()
        total = str(len(self._TRACKS))
        for label, track, _r, _w in self._TRACKS:
            tm = tracks[label].metadata
            self.assertEqual(
                (tm['movementnumber'], tm['movementtotal']),
                (str(track), total),
                "%s: movement %r of %r" % (label, tm['movementnumber'],
                                           tm['movementtotal']))

    def test_result_is_independent_of_lookup_order(self):
        baseline = None
        for order in ["fifo", "lifo"] + ["rand%d" % i for i in range(12)]:
            tracks = self._run_full_album(drain=order)
            result = {
                label: (self.mod.str_to_list(
                            tracks[label].metadata['~cwp_workid_top']),
                        self.mod.str_to_list(
                            tracks[label].metadata['~cwp_work_0']),
                        tracks[label].metadata['~cwp_part_levels'])
                for label, _t, _r, _w in self._TRACKS}
            if baseline is None:
                baseline = result
            else:
                self.assertEqual(result, baseline,
                                 "drain order %r changed the tags" % order)


class SwanLakeCrossAlbumMovementTotalIntegrationTestCase(ClassicalExtrasTestCase):
    """Guard against one album's movement count leaking into another.

    Picard registers ONE PartLevels for the session, so self.parts is shared by
    every album loaded. The movement total used to be stored there as
    self.parts[movementgroup]['movement-total'] -- keyed by work id alone, with
    no album in the key. Two releases of the same work therefore overwrote each
    other's total, and any album re-published afterwards reported the other
    one's count. Picard re-runs process_album whenever an album's files change,
    which happens constantly while mass tagging.

    Here the 19-track Jarvi Swan Lake (4e93a6a0) and the 43-track two-editions
    Swan Lake (a1a9e501) both hang off "Swan Lake, op. 20" 11f48c5e. Loading
    the 43-track album and then re-processing the 19-track one used to tag the
    latter movementtotal '19; 43'."""

    _JARVI_DIR = os.path.join(os.path.dirname(__file__), "fixtures",
                              "swanlake_jarvi")
    _FULL_DIR = os.path.join(os.path.dirname(__file__), "fixtures",
                             "swanlake_full")
    _JARVI_REL = "4e93a6a0-7858-4480-8420-b96c7eece538"
    _FULL_REL = "a1a9e501-a7df-45b4-9879-ddd9661d0f65"
    _GROUP = "11f48c5e-5ee9-4646-9826-fb7c2fccce7f"   # Swan Lake, op. 20
    # (label, track, recording id, work id)
    _JARVI_TRACKS = [
        ('t1', 1, '7cb0e24e-1feb-4116-9830-f1a4c195ace6',
         '58907391-15e4-3095-90a3-743583eb7039'),
        ('t2', 2, '9342b235-8a5f-45bb-9740-7415f6f94202',
         'd15ce760-99c4-36bc-b2d7-96a6e24b8e11'),
        ('t3', 3, '0a37744a-d383-45ed-bb32-793534e0afd0',
         'f32adc94-5198-347b-b275-08a6db82f686'),
        ('t4', 4, '372cc888-e785-45f4-bafa-5fed2fbfe782',
         'bafdc758-3521-3e27-952f-f724011b5f73'),
        ('t5', 5, 'c0f4ccdb-3b69-48b5-9b05-447abbb5258b',
         '44d081b5-3bce-42af-bf1f-c68191251bf6'),
        ('t6', 6, '82e19b56-1e0b-4105-a897-b4cbda402c66',
         '96ffa250-7241-315c-9816-1019e2a32416'),
        ('t7', 7, '3bdc898b-6238-447b-a93b-11f40a13fc37',
         'aea64fe4-1a6e-38d1-97b7-99a220634234'),
        ('t8', 8, '8e31cce7-5761-470d-98de-5a54a882df82',
         '3481d89d-95f0-4f74-afe6-02b33a9095ac'),
        ('t9', 9, 'd47f5329-0776-4aca-a988-655cdc9c9d2c',
         '0471148d-3968-40f3-a7b8-3bdb1e4ae176'),
        ('t10', 10, 'b3498fd0-52a6-4a6f-b3a4-68e088522664',
         'c6f93641-f036-4f18-a55d-dcbf183c68af'),
        ('t11', 11, '69ecf97c-1c09-4958-aaaf-2b8e0aca9078',
         '8ace07c4-0b91-4964-bac7-63cf103315d2'),
        ('t12', 12, '7cf85bde-35eb-4bc8-bb9a-71e70c8e49d1',
         'c057e48f-df20-4b40-ac1c-367474242c81'),
        ('t13', 13, 'fd852a5e-bb56-4eb7-b388-8b35ec2b5b08',
         '12818a2e-048a-475a-a279-66d8f90f4193'),
        ('t14', 14, '3b32df17-7a18-4b3b-b96b-d999709dbaed',
         '8d51db60-63d9-4d4c-9b26-16e8e29a99f6'),
        ('t15', 15, '21f888aa-5706-4f2b-9fe2-ee3270d781de',
         'c148e7d2-269b-4faf-9c93-82b79c5ef54c'),
        ('t16', 16, '5c7551fd-28c2-40b9-9cfa-b45e79d59cdd',
         '30f2e247-f345-4f3b-a7cc-09c09fc53e9b'),
        ('t17', 17, '663dfa49-8659-4579-a34e-38524452d588',
         '1a972d5e-ee15-4108-b16c-5585e54c1540'),
        ('t18', 18, '2180ee69-8cc0-49ac-a8af-2d018afa027a',
         'f12bea2c-7c34-47f9-9f68-66560e66a645'),
        ('t19', 19, '28721fbc-e331-4e92-8bfa-dd76ea12bc55',
         '5e8125e0-20d4-4311-a9ab-1dc29a9f6dc9'),
    ]

    def setUp(self):
        super().setUp()
        self.set_config_values(setting={
            "server_host": "musicbrainz.org", "server_port": 443,
            "use_cache": True, "classical_work_parts": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": True, "cwp_collections": True,
            "crr_recording_lookup": False,
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "artist_locales": ["en"], "translate_artist_names": False,
            "translate_artist_names_script_exception": False,
        })

    @staticmethod
    def _read(directory, name):
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            return json.load(f)

    def _load_album(self, pl, release_id, rows, fixdir, album_name):
        """Run one album through the shared PartLevels; return (tracks, album).

        Work fixtures are looked up in BOTH albums' directories -- the two
        releases share most of their work hierarchy, which is the point.
        """
        from unittest.mock import Mock
        pending = []

        def get(host, port, path, cb, **kw):
            wid = path.rsplit("/", 1)[-1]
            for d in (self._FULL_DIR, self._JARVI_DIR):
                fn = os.path.join(d, "work_%s.json" % wid)
                if os.path.exists(fn):
                    pending.append((cb, self._read(d, "work_%s.json" % wid)))
                    return
            raise FileNotFoundError(wid)

        tagger = Mock()
        tagger.webservice.get = get
        album = Mock()
        album._requests = 0
        album._new_tracks = []
        album.tagger = tagger
        album._finalize_loading = lambda _a: None

        opts = dict(_ALL_OPTION_DEFAULTS)
        opts.update({
            "classical_work_parts": True, "use_cache": True,
            "cwp_partial": True, "cwp_arrangements": True,
            "cwp_medley": True, "cwp_collections": True,
            "cwp_aliases": False, "cwp_aliases_tag_text": "",
            "log_error": False, "log_warning": False,
            "log_debug": False, "log_info": False,
            "crr_recording_lookup": False,
        })
        opts["cwp_removewords_p"] = opts.get("cwp_removewords", "")

        from picard.metadata import Metadata
        tracks = {}
        for row in rows:
            if len(row) == 5:                      # full album: has a disc no.
                label, disc, track, rec_id, work_id = row
                recfile = "rec_%s.json" % label
            else:
                label, track, rec_id, work_id = row
                disc = 1
                recfile = "rec_%s.json" % label
            tm = Metadata()
            tm['musicbrainz_albumid'] = release_id
            tm['musicbrainz_recordingid'] = rec_id
            tm['musicbrainz_workid'] = work_id
            tm['album'] = album_name
            tm['title'] = label
            tm['tracknumber'] = str(track)
            tm['discnumber'] = str(disc)
            tm['~ce_options'] = repr(opts)
            t = Mock(name=label)
            t.metadata = tm
            t._id = release_id + label
            t.__hash__ = lambda self: hash(self._id)
            t.__eq__ = lambda self, o: getattr(o, "_id", None) == self._id
            tracks[label] = t
            album._new_tracks.append(t)
            pl.add_work_info(album, tm,
                             {'recording': self._read(fixdir, recfile)}, {})
        while pending:
            cb, resp = pending.pop(0)
            cb(resp, None, None)
        return tracks, album

    def _shared_partlevels(self):
        mod = self.mod
        saved = (mod.get_aliases, mod.close_log)
        mod.get_aliases = lambda *a, **k: None
        mod.close_log = lambda *a, **k: None
        self.addCleanup(lambda: setattr(mod, "get_aliases", saved[0]))
        self.addCleanup(lambda: setattr(mod, "close_log", saved[1]))
        pl = mod.PartLevels()
        pl.process_work_artists = lambda *a, **k: None
        return pl

    def test_movement_total_survives_another_album_and_a_reprocess(self):
        """The reported bug: mass tagging gave a 19-track release a movement
        total taken from a different Swan Lake album."""
        pl = self._shared_partlevels()
        jarvi, jarvi_album = self._load_album(
            pl, self._JARVI_REL, self._JARVI_TRACKS, self._JARVI_DIR,
            "Swan Lake")
        self._load_album(
            pl, self._FULL_REL,
            SwanLakeFullBalletTwoVersionsIntegrationTestCase._TRACKS,
            self._FULL_DIR, "Swan Lake (complete)")
        # Picard re-runs process_album whenever the album's files change.
        pl.process_album(self._JARVI_REL, jarvi_album)
        for label, track, _r, _w in self._JARVI_TRACKS:
            tm = jarvi[label].metadata
            self.assertEqual(
                (tm['movementnumber'], tm['movementtotal']),
                (str(track), '19'),
                "%s: movement %r of %r" % (label, tm['movementnumber'],
                                           tm['movementtotal']))

    def test_each_album_keeps_its_own_total(self):
        """Both albums share the top work, so their totals must be kept apart:
        19 for the Jarvi release, 43 for the complete one."""
        pl = self._shared_partlevels()
        jarvi, _ = self._load_album(
            pl, self._JARVI_REL, self._JARVI_TRACKS, self._JARVI_DIR,
            "Swan Lake")
        full, _ = self._load_album(
            pl, self._FULL_REL,
            SwanLakeFullBalletTwoVersionsIntegrationTestCase._TRACKS,
            self._FULL_DIR, "Swan Lake (complete)")
        self.assertEqual(
            {jarvi[l].metadata['movementtotal']
             for l, _t, _r, _w in self._JARVI_TRACKS}, {'19'})
        self.assertEqual(
            {full[l].metadata['movementtotal']
             for l, _d, _t, _r, _w in
             SwanLakeFullBalletTwoVersionsIntegrationTestCase._TRACKS}, {'43'})


if __name__ == "__main__":
    unittest.main()
