# -*- coding: utf-8 -*-
"""Tests for hierarchy.session: the pure recording place/date tag module.

These mirror the legacy plugin's RecordingSessionTagsTestCase, over the same
real MusicBrainz fixtures, proving the move into the Picard-free package
preserves the legacy's behaviour exactly.
"""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "plugins", "classical_extras"))

from hierarchy.session import recording_session_tags  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _relations(fixture_name):
    with open(os.path.join(FIXTURES, fixture_name), encoding="utf-8") as f:
        return json.load(f)["relations"]


class HierarchySessionTagsTestCase(unittest.TestCase):
    """recording_session_tags over the recorded fixtures."""

    def test_one_place_single_date(self):
        relations = _relations("rec_92ab0819_fanfare.json")
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Atlanta Symphony Hall"])
        self.assertEqual(tags["recordingcity"], ["Atlanta"])
        self.assertEqual(tags["recordingdate"], "1982-05-24")
        self.assertEqual(tags["recordingsessions"],
                         ["Atlanta Symphony Hall, Atlanta (1982-05-24)"])

    def test_two_places_conductor_span_no_extra_session(self):
        relations = _relations("rec_791581ad_bruckner6.json")
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingsessions"],
                         ["サントリーホール, Akasaka (2018-04-19)",
                          "横浜みなとみらいホール, Minato-Mirai (2018-04-22)"])
        self.assertEqual(tags["recordingplace"],
                         ["サントリーホール", "横浜みなとみらいホール"])
        self.assertEqual(tags["recordingcity"], ["Akasaka", "Minato-Mirai"])
        self.assertEqual(tags["recordingdate"], "2018-04-19 - 2018-04-22")
        self.assertEqual(len(tags["recordingsessions"]), 2)

    def test_no_place_date_only_fallback(self):
        relations = _relations("rec_9dcd4293_redpony.json")
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], [])
        self.assertEqual(tags["recordingcity"], [])
        self.assertEqual(tags["recordingdate"], "1963-03-25")
        self.assertEqual(tags["recordingsessions"], ["(1963-03-25)"])

    def test_precision_match_month_collapse_to_single(self):
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "1981-03", "end": "1981-03",
             "place": {"name": "Symphony Hall", "area": {"name": "Boston"}}},
            {"target-type": "artist", "type": "conductor",
             "begin": "1981-03", "end": "1981-03",
             "artist": {"name": "Ozawa"}},
        ]
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingdate"], "1981-03")
        self.assertEqual(tags["recordingsessions"],
                         ["Symphony Hall, Boston (1981-03)"])
        self.assertEqual(len(tags["recordingsessions"]), 1)

    def test_duplicate_venue_dedup_in_flat_tags(self):
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "2024-09-11", "end": "2024-09-11",
             "place": {"name": "Suntory Hall", "area": {"name": "Tokyo"}}},
            {"target-type": "place", "type": "recorded at",
             "begin": "2024-09-12", "end": "2024-09-12",
             "place": {"name": "Suntory Hall", "area": {"name": "Tokyo"}}},
        ]
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Suntory Hall"])
        self.assertEqual(tags["recordingcity"], ["Tokyo"])
        self.assertEqual(len(tags["recordingsessions"]), 2)
        self.assertEqual(tags["recordingdate"], "2024-09-11 - 2024-09-12")

    def test_place_without_city_omits_stray_comma(self):
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "2000-06-01", "end": "2000-06-01",
             "place": {"name": "Studio X", "area": {}}},
        ]
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Studio X"])
        self.assertEqual(tags["recordingcity"], [])
        self.assertEqual(tags["recordingsessions"], ["Studio X (2000-06-01)"])
        self.assertEqual(tags["recordingdate"], "2000-06-01")

    def test_dateless_place_with_dated_conductor_no_crash(self):
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "", "end": "",
             "place": {"name": "Abbey Road Studios",
                       "area": {"name": "London"}}},
            {"target-type": "artist", "type": "conductor",
             "begin": "1970-01-01", "end": "1970-01-01",
             "artist": {"name": "George Martin"}},
        ]
        tags = recording_session_tags(relations)
        self.assertEqual(tags["recordingplace"], ["Abbey Road Studios"])
        self.assertEqual(tags["recordingcity"], ["London"])
        self.assertEqual(tags["recordingdate"], "1970-01-01")
        self.assertEqual(tags["recordingsessions"],
                         ["Abbey Road Studios, London", "(1970-01-01)"])

    def test_name_overrides_keyed_by_place_id(self):
        """The name_overrides path: a romanized venue name keyed by the
        place's MusicBrainz id replaces the canonical name."""
        relations = [
            {"target-type": "place", "type": "recorded at",
             "begin": "2001-01-01", "end": "2001-01-01",
             "place": {"id": "place-1", "name": "さいたまスーパーアリーナ",
                       "area": {"id": "area-1", "name": "Saitama"}}},
        ]
        tags = recording_session_tags(
            relations, name_overrides={"place-1": "Saitama Super Arena"})
        self.assertEqual(tags["recordingplace"], ["Saitama Super Arena"])
        self.assertEqual(tags["recordingcity"], ["Saitama"])
        self.assertEqual(tags["recordingsessions"],
                         ["Saitama Super Arena, Saitama (2001-01-01)"])

    def test_no_picard_import_in_module(self):
        """The module must stay Picard-free: it neither imports picard nor
        references the plugin package."""
        import hierarchy.session
        with open(hierarchy.session.__file__.replace('.pyc', '.py'),
                  encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("import picard", source)
        self.assertNotIn("from picard", source)


if __name__ == "__main__":
    unittest.main()
