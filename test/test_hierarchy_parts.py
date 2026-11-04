"""The part-name strip: a work's name with the name of the work above it out.

The cases are the recorded legacy tags - the strip has to reproduce what
the legacy wrote for the same names, or the tag is a documented
difference. They need no Picard.
"""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "plugins", "classical_extras"))

from hierarchy.parts import (  # noqa: E402
    strip_excess_punctuation,
    strip_parent_name,
)


class StripParentNameTestCase(unittest.TestCase):
    """The strip, against the cases the recorded tags hold."""

    def test_the_work_name_loses_the_parents_name(self):
        """daphnis d1t1, level 1: the part is what the parent's name
        covered."""
        self.assertEqual(
            strip_parent_name(
                "Daphnis et Chloé: Tableau I. No. 1. Introduction et danse "
                "religieuse",
                "Daphnis et Chloé: Tableau I"),
            "No. 1. Introduction et danse religieuse")

    def test_a_cyrillic_name_strips_the_same_way(self):
        """nutcracker d1t1, level 0: the op. number's punctuation does not
        get in the way."""
        self.assertEqual(
            strip_parent_name("Щелкунчик, op. 71: Увертюра",
                              "Щелкунчик, op. 71"),
            "Увертюра")

    def test_a_work_whose_name_is_the_parents_is_stripped_empty(self):
        """daphnis d1t1, level 0: the recording stands in for the work the
        level above names, and the part is the empty string."""
        self.assertEqual(
            strip_parent_name(
                "Daphnis et Chloé: Tableau I. No. 1. Introduction et danse "
                "religieuse",
                "Daphnis et Chloé: Tableau I. No. 1. Introduction et danse "
                "religieuse"),
            "")

    def test_a_match_after_other_text_leaves_an_ellipsis(self):
        """When the parent's name sits inside the work name rather than at
        its start, the text before it survives, marked with an ellipsis."""
        self.assertEqual(
            strip_parent_name(
                "The Work: the suite from The Ballet",
                "The Ballet"),
            "The Work: the suite from …")

    def test_a_common_run_of_words_is_eliminated(self):
        """The suite-named work whose parent names the poem: the primary
        strip never matches, so the common-word fallback eliminates the
        longest run the two names share - as the recorded tag shows."""
        self.assertEqual(
            strip_parent_name(
                "L’Oiseau de feu: III. Berceuse",
                "L’Oiseau de feu, suite de 1919"),
            "III. Berceuse")

    def test_a_run_that_starts_mid_name_leaves_an_ellipsis(self):
        """The firebird suite's variation: the common run sits after the
        movement's number, so what was taken is marked."""
        self.assertEqual(
            strip_parent_name(
                "L’Oiseau de feu: I. Variation de l’Oiseau de feu",
                "L’Oiseau de feu, suite de 1919"),
            "I. Variation de …")

    def test_a_roman_numeral_is_folded_for_the_comparison(self):
        """The requiem's 'IIIa' against the parent's 'III': the boiling
        folds the numerals, the common run spans them, and the part keeps
        its own numbering - as the recorded tag shows."""
        self.assertEqual(
            strip_parent_name(
                "Requiem in D minor, K. 626: IIIa. Sequenz: “Dies irae”",
                "Requiem in D minor, K. 626: III. Sequenz"),
            "IIIa. Sequenz: “Dies irae”")

    def test_the_survivor_is_cleaned_of_orphan_punctuation(self):
        """A separator left behind by the match is taken away - the
        legacy cleans the survivor through strip_excess_punctuation."""
        self.assertEqual(
            strip_parent_name(
                "Le Sacre du printemps: I. L’Adoration de la terre: "
                "I. Introduction",
                "Le Sacre du printemps: I. L’Adoration de la terre"),
            "I. Introduction")

    def test_a_list_parent_is_joined(self):
        """A parent named by several works strips by them all."""
        self.assertEqual(
            strip_parent_name(
                "The Ballet: Suite no. 5: IV. Finale",
                ["The Ballet", "Suite no. 5"]),
            "IV. Finale")


class StripExcessPunctuationTestCase(unittest.TestCase):
    """The cleaner the strip hands its survivor to."""

    def test_orphan_brackets_and_separators_go(self):
        self.assertEqual(strip_excess_punctuation("- ( Introduction ]"),
                         "Introduction")

    def test_the_quote_rules_of_the_legacy_hold(self):
        """Two quotes of a kind stay; one apostrophe followed by a space goes
        with the space - the legacy counts the quotes first."""
        self.assertEqual(
            strip_excess_punctuation("' celo '"), "' celo '")
        self.assertEqual(
            strip_excess_punctuation("' celo"), "celo")

    def test_an_ordinary_name_passes_through(self):
        self.assertEqual(
            strip_excess_punctuation("No. 6. Nocturne. Danse lente"),
            "No. 6. Nocturne. Danse lente")


if __name__ == "__main__":
    unittest.main()
