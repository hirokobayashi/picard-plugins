#!/usr/bin/env python
# coding: utf-8
"""Regression tests for the genre multi-value bug (Task E).

The bug: with Picard's "Use folksonomy tags as genre" (folksonomy_tags) option
off and the plugin's "apply filter to genres" (cwp_genres_filter) option off,
genres delivered by MusicBrainz/Picard as a single joined string (e.g.
``"Classical; Symphony"`` when Picard's "join_genres" option is set) were
written to the file as ONE tag value containing the joined string instead of
SEPARATE multi-value tag entries.

The fix normalises ``tm['genre']`` to a proper multi-value list in the
folksonomy-off branch of ``map_tags`` so the genres are always written as
separate values regardless of the folksonomy / filter combination.

Reported against release
https://musicbrainz.org/release/9dcd4293-9ae9-4f76-91f1-c5b629787796
"""
import copy
import os
import sys
import unittest

from test.plugin_test_case import PluginTestCase

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


class GenreFilterOff(PluginTestCase):
    MOD = "classical_extras"
    _mod = None

    def setUp(self):
        super().setUp()
        for _stale in ('const', 'picard.plugins.classical_extras.const',
                       'picard.plugins.classical_extras'):
            sys.modules.pop(_stale, None)
        _cfg = dict(_ALL_OPTION_DEFAULTS)
        _cfg['folksonomy_tags'] = True
        _cfg['enabled_plugins'] = [self.MOD]
        self.set_config_values(setting=_cfg)
        if GenreFilterOff._mod is None:
            GenreFilterOff._mod = self._test_plugin_install(
                "Classical Extras", self.MOD)
        self.mod = GenreFilterOff._mod

    def _run(self, opts, genre_value):
        """Run map_tags with the given options and initial ``genre`` value."""
        from picard.metadata import Metadata
        ce = self.mod
        options = dict(_ALL_OPTION_DEFAULTS)
        options.update(opts)
        tm = Metadata()
        tm['genre'] = genre_value
        tm['~cea_artists_complete'] = 'Y'
        tm['~cea_works_complete'] = 'Y'
        tm['~cea_album_composer_lastnames'] = ''
        tm['album'] = 'Test'
        tm['~cea_performers'] = ''
        tm['title'] = 'Test'
        ce.map_tags(options, 'test', 'alb', tm)
        return tm.getall(options['cwp_genre_tag']), tm

    def _assert_split(self, vals):
        self.assertGreaterEqual(
            len(vals), 2,
            "pre-joined genre must be split into >=2 values, got %r" % (vals,))
        for v in vals:
            self.assertNotIn(';', v, "genre value still joined: %r" % (v,))

    def test_folksonomy_off_filter_off_prejoined_string(self):
        """folksonomy OFF + filter OFF + genre as a bare joined string."""
        from picard import config
        config.setting['folksonomy_tags'] = False
        try:
            vals, _ = self._run({'cwp_genres_filter': False},
                                'Classical; Symphony')
            self._assert_split(vals)
        finally:
            config.setting['folksonomy_tags'] = True

    def test_folksonomy_off_filter_off_prejoined_single_element_list(self):
        """folksonomy OFF + filter OFF + genre stored as a one-element list
        containing the joined string - the exact form Picard produces when its
        ``join_genres`` option is set (``tm['genre'] = ['Classical; Symphony']``).
        """
        from picard import config
        config.setting['folksonomy_tags'] = False
        try:
            vals, _ = self._run({'cwp_genres_filter': False},
                                ['Classical; Symphony'])
            self._assert_split(vals)
        finally:
            config.setting['folksonomy_tags'] = True

    def test_folksonomy_on_filter_off_prejoined(self):
        """folksonomy ON + filter OFF already splits (folksonomy branch
        normalises via str_to_list); ensure it stays split."""
        from picard import config
        config.setting['folksonomy_tags'] = True
        vals, _ = self._run({'cwp_genres_filter': False},
                            'Classical; Symphony')
        self._assert_split(vals)

    def test_folksonomy_off_filter_on_prejoined(self):
        """folksonomy OFF + filter ON: a pre-joined genre is normalised and the
        filter rebuilds the genre tag with separate values."""
        from picard import config
        config.setting['folksonomy_tags'] = False
        try:
            vals, _ = self._run({'cwp_genres_filter': True},
                                'Classical; Symphony')
            self._assert_split(vals)
        finally:
            config.setting['folksonomy_tags'] = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
