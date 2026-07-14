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

    def _make(self, release_id):
        from picard.metadata import Metadata
        pl = self.mod.PartLevels()
        pl.process_album = lambda rid, alb: self._process_calls.append(rid)
        self._process_calls = []
        tm = Metadata()
        tm['musicbrainz_albumid'] = release_id
        track = self._FakeTrack(tm)
        album = self._FakeAlbum()
        album._requests = 1
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


if __name__ == "__main__":
    unittest.main()
