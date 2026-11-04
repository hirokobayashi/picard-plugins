"""The work metadata: the genres and dates a work response carries.

The cases are the recorded work responses; the extraction has to give
what the legacy's tags read as. No Picard needed.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "plugins", "classical_extras"))

from hierarchy.workmeta import (  # noqa: E402
    WorkMeta,
    work_metadata,
)


def _load(path):
    import json
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class WorkMetaTestCase(unittest.TestCase):
    """The extraction, against the recorded responses."""

    def test_the_work_type_is_the_worktype_genre(self):
        """The Daphnis ballet: the type the legacy writes as a genre."""
        meta = work_metadata(_load(os.path.join(
            os.path.dirname(__file__), "fixtures", "daphnis",
            "work_a55d2dfe-fb81-42e8-9e0f-3e6fd9654ca1.json")))
        self.assertEqual(meta.worktype_genres, ["Ballet"])
        self.assertEqual(meta.composed_dates, ["1909-1912"])

    def test_a_full_date_is_taken_to_its_year(self):
        """The Swan Lake premiere: 1895-01-27 reads as 1895."""
        meta = work_metadata(_load(os.path.join(
            os.path.dirname(__file__), "fixtures", "swanlake_full",
            "work_13867eb1-42c2-45ed-97d9-e1cc85b006fd.json")))
        self.assertEqual(meta.premiered_dates, ["1895"])
        self.assertEqual(meta.composed_dates, ["1876"])

    def test_a_work_without_the_relations_has_no_dates(self):
        """A movement whose composer carries no dates contributes
        nothing."""
        meta = work_metadata(_load(os.path.join(
            os.path.dirname(__file__), "fixtures", "swanlake_full",
            "work_58907391-15e4-3095-90a3-743583eb7039.json")))
        self.assertEqual(meta.composed_dates, [])
        self.assertEqual(meta.premiered_dates, [])
        self.assertEqual(meta.worktype_genres, [])

    def test_publishing_dates_come_from_label_relations(self):
        """The Firebird suite's publishing: two label relations, two
        years."""
        meta = work_metadata(_load(os.path.join(
            os.path.dirname(__file__), "fixtures", "firebird",
            "work_54dacada-b00c-34b6-bbbb-e89855c7219f.json")))
        self.assertEqual(meta.published_dates, ["1913", "1921"])

    def test_an_empty_response_is_all_empty(self):
        """A response that names nothing carries nothing."""
        meta = work_metadata({})
        self.assertEqual(meta.folks_genres, [])
        self.assertEqual(meta.worktype_genres, [])
        self.assertEqual(meta.composed_dates, [])
        self.assertEqual(meta.premiered_dates, [])
        self.assertEqual(meta.published_dates, [])

    def test_a_differing_list_keeps_the_legacy_spellings(self):
        """The composer dates collapse an agreeing pair; the published
        dates of a differing list write every pair as a range - the
        legacy's two branches never agreed with each other."""
        response = {"relations": [
            {"target-type": "artist", "type": "composer",
             "begin": "1913", "end": "1913"},
            {"target-type": "artist", "type": "composer",
             "begin": "1920", "end": "1924"},
            {"target-type": "label", "type": "publishing",
             "begin": "1913", "end": "1913"},
            {"target-type": "label", "type": "publishing",
             "begin": "1920", "end": "1924"},
        ]}
        meta = work_metadata(response)
        self.assertEqual(meta.composed_dates, ["1913", "1920-1924"])
        self.assertEqual(meta.published_dates, ["1913-1913", "1920-1924"])

    def test_the_key_comes_from_the_work_attributes(self):
        """The Nutcracker suite's overture: the Key attribute's value."""
        meta = work_metadata(_load(os.path.join(
            os.path.dirname(__file__), "fixtures", "nutcracker",
            "work_fce0c96b-42a9-4c41-a455-d5dc017bcb44.json")))
        self.assertEqual(meta.keys, ["B-flat major"])

    def test_a_work_without_a_key_attribute_has_no_key(self):
        """The Swan Lake act movement carries no Key attribute."""
        meta = work_metadata(_load(os.path.join(
            os.path.dirname(__file__), "fixtures", "swanlake_full",
            "work_58907391-15e4-3095-90a3-743583eb7039.json")))
        self.assertEqual(meta.keys, [])

    def test_the_primary_alias_of_a_locale(self):
        """An alias carries its locale; only the primary ones count."""
        meta = work_metadata({"aliases": [
            {"locale": "en", "primary": True, "name": "The Winter Work"},
            {"locale": "en", "primary": False, "name": "A Draft"},
            {"locale": "de", "primary": True, "name": "Die Winterarbeit"},
            {"locale": None, "primary": True, "name": "No Locale"},
        ]})
        self.assertEqual(meta.aliases.get("en"), "The Winter Work")
        self.assertEqual(meta.aliases.get("de"), "Die Winterarbeit")
        self.assertNotIn(None, meta.aliases)

    def test_the_functional_spelling_agrees(self):
        self.assertEqual(
            work_metadata({"type": "Ballet"}).worktype_genres,
            WorkMeta.of({"type": "Ballet"}).worktype_genres)


if __name__ == "__main__":
    unittest.main()
