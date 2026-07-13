#!/usr/bin/env python
# coding: utf-8
import collections, copy, unittest, os, sys
from test.plugin_test_case import PluginTestCase
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "classical_extras"))
import const as _ce_const
sys.path.pop(0)

_ALL_OPTION_DEFAULTS = {}
for _group in ("ARTISTS_OPTIONS","TAG_OPTIONS","TAG_DETAIL_OPTIONS","WORKPARTS_OPTIONS","GENRE_OPTIONS","PICARD_OPTIONS","OTHER_OPTIONS"):
    for _o in getattr(_ce_const, _group):
        _ALL_OPTION_DEFAULTS[_o["option"]] = copy.deepcopy(_o["default"])

class GenreRepro(PluginTestCase):
    MOD = "classical_extras"
    _mod = None

    def setUp(self):
        super().setUp()
        # The plugin imports its const as `picard.plugins.classical_extras.const`
        # but references it as the bare name `const`. When another test class
        # loads then unloads this plugin, the package is removed from
        # `sys.modules` but the `.const` submodule is left behind as a stale
        # entry. On re-import the stale submodule is reused and the bare
        # `const` name is never bound in the new package globals, causing a
        # `NameError`. Clear both entries so each install re-imports cleanly.
        for _stale in ('const', 'picard.plugins.classical_extras.const',
                       'picard.plugins.classical_extras'):
            sys.modules.pop(_stale, None)
        # add Picard built-in options the plugin reads via config.setting
        _cfg = dict(_ALL_OPTION_DEFAULTS)
        _cfg['folksonomy_tags'] = True
        # Always (re)set enabled_plugins: PluginTestCase.setUp resets config to
        # an empty dict every test, and the plugin module is cached after the
        # first install so `_test_plugin_install` (which normally sets this)
        # only runs once. Without this, later tests/other suites reading
        # `config.setting['enabled_plugins']` hit a KeyError.
        _cfg['enabled_plugins'] = [self.MOD]
        self.set_config_values(setting=_cfg)
        if GenreRepro._mod is None:
            GenreRepro._mod = self._test_plugin_install("Classical Extras", self.MOD)
        self.mod = GenreRepro._mod

    def test_genre_multi_value_written_separately(self):
        """Several folksonomy genres that match the filter should be written
        as SEPARATE multi-value entries, not collapsed into one string."""
        from picard.metadata import Metadata
        ce = self.mod
        options = dict(_ALL_OPTION_DEFAULTS)
        # defaults: cwp_genre_tag='genre', cwp_genres_filter=True
        release_id = 'test'
        tm = Metadata()
        # multiple folksonomy genres that are all in the classical main/sub lists
        # Classical (main), Symphony (main), Avant-garde (sub), Minimalism (sub)
        tm['genre'] = ['Classical', 'Symphony', 'Avant-garde', 'Minimalism']
        tm['~cea_artists_complete'] = 'Y'
        tm['~cea_works_complete'] = 'Y'
        tm['~cea_album_composer_lastnames'] = ''
        tm['album'] = 'Test'
        tm['~cea_performers'] = ''
        tm['title'] = 'Test'

        # call map_tags (the real function)
        ce.map_tags(options, release_id, 'alb', tm)

        genre_values = tm.getall(options['cwp_genre_tag'])
        subgenre_values = tm.getall(options['cwp_subgenre_tag'])
        print('GENRE getall:', genre_values)
        print('GENRE getitem:', repr(tm[options['cwp_genre_tag']]))
        print('SUBGENRE getall:', subgenre_values)
        print('SUBGENRE getitem:', repr(tm[options['cwp_subgenre_tag']]))
        # Expect multiple separate values, each its own element
        self.assertGreater(len(genre_values), 1, "main genre should have >1 separate value")
        self.assertGreater(len(subgenre_values), 1, "sub-genre should have >1 separate value")
        # None of the individual values should itself contain a separator
        for v in genre_values:
            self.assertNotIn(';', v, "main genre value still joined: %r" % (v,))
        for v in subgenre_values:
            self.assertNotIn(';', v, "sub-genre value still joined: %r" % (v,))

    def test_prejoined_genre_string_split_when_folksonomy_off(self):
        """Regression: when folksonomy_tags is OFF the genre is not split/
        deleted before writing, so a pre-joined string like 'Classical;
        Avant-garde' (as delivered by some MusicBrainz/Picard paths) must be
        split into SEPARATE multi-value tag entries, not preserved as one
        joined value. This is the real-world bug from release
        9dcd4293-9ae9-4f76-91f1-c5b629787796.
        """
        from picard.metadata import Metadata
        ce = self.mod
        # folksonomy_tags is a Picard core option read from config.setting
        from picard import config
        config.setting['folksonomy_tags'] = False
        options = dict(_ALL_OPTION_DEFAULTS)
        release_id = 'test'
        tm = Metadata()
        tm['genre'] = 'Classical; Avant-garde'
        tm['~cea_artists_complete'] = 'Y'
        tm['~cea_works_complete'] = 'Y'
        tm['~cea_album_composer_lastnames'] = ''
        tm['album'] = 'Test'
        tm['~cea_performers'] = ''
        tm['title'] = 'Test'

        ce.map_tags(options, release_id, 'alb', tm)

        genre_values = tm.getall(options['cwp_genre_tag'])
        print('PREJOINED GENRE getall:', genre_values)
        # The joined string must have been split: each genre is its own value
        self.assertGreaterEqual(len(genre_values), 2,
                                "pre-joined genre should be split into >=2 "
                                "values, got %r" % (genre_values,))
        for v in genre_values:
            self.assertNotIn(';', v,
                             "genre value still joined: %r" % (v,))
        # Restore the config the rest of the suite expects
        config.setting['folksonomy_tags'] = True

    def _make_partlevels(self, options):
        ce = self.mod
        pl = ce.PartLevels()
        pl.options = collections.defaultdict(dict)
        track = 'Test Track'
        pl.options[track] = options
        pl.SEPARATORS = ['; ', '/ ', ';', '/']
        pl.parts = collections.defaultdict(
            lambda: collections.defaultdict(dict))
        return pl, track

    def test_write_tags_then_map_tags_multi_value(self):
        """Work-level folks + worktype genres flow through write_tags into
        ~cwp_candidate_genres, then map_tags writes the genre tag with each
        genre as a separate value."""
        from picard.metadata import Metadata
        ce = self.mod
        options = dict(_ALL_OPTION_DEFAULTS)
        options['cwp_genres_use_folks'] = True
        options['cwp_genres_use_worktype'] = True
        options['cwp_genres_filter'] = True
        release_id = 'test'
        tm = Metadata()
        tm['album'] = 'Test'
        tm['title'] = 'Test'
        tm['~cea_artists_complete'] = 'Y'
        tm['~cea_works_complete'] = 'Y'
        tm['~cea_album_composer_lastnames'] = ''
        tm['~cea_performers'] = ''

        pl, track = self._make_partlevels(options)
        wid = ('w-111',)
        pl.parts[wid]['folks_genres'] = ['Classical', 'Symphony', 'Avant-garde']
        pl.parts[wid]['worktype_genres'] = ['Symphony']
        pl.parts[wid]['key'] = []
        pl.parts[wid]['composed_dates'] = []
        pl.parts[wid]['published_dates'] = []
        pl.parts[wid]['premiered_dates'] = []

        pl.write_tags(release_id, track, tm, wid)
        print('AFTER write_tags candidate getall:',
              tm.getall('~cwp_candidate_genres'))

        ce.map_tags(options, release_id, 'alb', tm)

        genre = tm.getall(options['cwp_genre_tag'])
        subgenre = tm.getall(options['cwp_subgenre_tag'])
        print('FINAL genre getall:', genre)
        print('FINAL genre getitem:', repr(tm[options['cwp_genre_tag']]))
        print('FINAL subgenre getall:', subgenre)
        print('FINAL subgenre getitem:', repr(tm[options['cwp_subgenre_tag']]))

        self.assertGreater(len(genre), 1,
                           "main genre should be multiple separate values, got %r" % (genre,))
        for v in genre:
            self.assertNotIn(';', v, "genre value still joined: %r" % (v,))
        for v in subgenre:
            self.assertNotIn(';', v, "subgenre value still joined: %r" % (v,))

if __name__ == "__main__":
    unittest.main(verbosity=2)
