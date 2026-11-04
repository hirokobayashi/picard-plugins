"""The part name: a work's name with the name of the work above it taken out.

The legacy's ``~cwp_part_N`` holds each level's work names with the name
of the level above stripped away - under the work "Daphnis et Chloé:
Tableau I", the part of "Daphnis et Chloé: Tableau I. No. 6. Nocturne"
is "No. 6. Nocturne". The strip is textual: the parent's punctuation is
normalised to spaces, its words are matched against the work name
allowing up to two stray non-letters between them, and whatever the
match leaves is the part. A match that starts after other text leaves an
ellipsis where the parent's words were taken from.

When the parent's name never matches - a work named by a *suite* whose
parent names the *poem*, say - the legacy falls back to eliminating the
longest common run of words, compared after boiling (punctuation,
capitals, accents and roman numerals folded away). The fallback is
ported here without the legacy's synonym canonisation: the words are
compared as the names spell them, which is what the plugin's default
synonym tables reduce to on the recorded releases.
"""

from __future__ import annotations

import re
import unicodedata

#: Roman numerals the boiling folds away, so that "III" and "3" compare
#: equal. The pattern and the ported conversion are the legacy's.
ROMAN_NUMERALS = (r"\b((?=[MDCLXVI])(M{0,4}(CM|CD|D?)?C{0,3}(XC|XL|L?)?"
                  r"X{0,3}(IX|IV|V?)?I{0,3}))(?:\.|-|:|;|,|\s|$)")
RE_ROMANS = re.compile(ROMAN_NUMERALS, re.IGNORECASE)
ROMAN_MAP = (("M", 1000), ("CM", 900), ("D", 500), ("CD", 400), ("C", 100),
             ("XC", 90), ("L", 50), ("XL", 40), ("X", 10), ("IX", 9),
             ("V", 5), ("IV", 4), ("I", 1),
             ("m", 1000), ("cm", 900), ("d", 500), ("cd", 400), ("c", 100),
             ("xc", 90), ("l", 50), ("xl", 40), ("x", 10), ("ix", 9),
             ("v", 5), ("iv", 4), ("i", 1))

#: The legacy's word splitter: a word may carry an internal apostrophe,
#: and a lone ampersand is a word of its own.
WORD = re.compile(r"(?:\b|\"|\')(\w+[’]?\w*)|(?:\b\w+\b)|(\B\&\B)")

#: How many boiled words a common run must span before the fallback
#: eliminates it - the legacy's ``cwp_common_chars`` default.
MIN_COMMON_WORDS = 2


def strip_excess_punctuation(s: str) -> str:
    """Remove orphan punctuation, unmatched quotes and brackets.

    Ported from the legacy's function of the same name.
    """
    if s:
        s_prev = ''
        counter = 0
        while s != s_prev:
            if counter > 100:
                break  # safety valve
            s_prev = s
            s = s.replace('  ', ' ')
            s = s.strip("&.-:;, ")
            s = s.lstrip("!)]}")
            s = s.rstrip("([{")
            s = s.lstrip("\u2019")  # Right single quote
            s = s.lstrip("\u201D")  # Right double quote
            if s.count("\u201E") == 0:  # u201E is lower double quote (German etc.)
                # only strip if there is no German-style lower quote present
                s = s.rstrip("\u201C")
            s = s.rstrip("\u2018")  # Left single quote
            if s.count('"') % 2 != 0:
                s = s.strip('"')
            if s.count("'") % 2 != 0:
                s = s.strip("'")
            if len(s) > 0 and s[0] == "\u201C" and s.count("\u201D") == 0:
                s = s.lstrip("\u201C")
            if len(s) > 0 and s[-1] == "\u201D" and s.count("\u201C") == 0 \
                    and s.count("\u201E") == 0:
                # only strip if there is no German-style lower quote present
                s = s.rstrip("\u201D")
            if len(s) > 0 and s[0] == "\u2018" and s.count("\u2019") == 0:
                s = s.lstrip("\u2018")
            if len(s) > 0 and s[-1] == "\u2019" and s.count("\u2018") == 0:
                s = s.rstrip("\u2019")
            if s:
                if s.count('"') == 1:
                    s = s.replace('"', '')
                if s.count("'") == 1:
                    s = s.replace(" '", " ")
                if "(" in s and ")" not in s:
                    s = s.replace("(", "")
                if ")" in s and "(" not in s:
                    s = s.replace(")", "")
                if "[" in s and "]" not in s:
                    s = s.replace("[", "")
                if "]" in s and "[" not in s:
                    s = s.replace("]", "")
                if "{" in s and "}" not in s:
                    s = s.replace("{", "")
                if "}" in s and "{" not in s:
                    s = s.replace("}", "")
            if s:
                match_chars = [("(", ")"), ("[", "]"), ("{", "}")]
                last = len(s) - 1
                for char_pair in match_chars:
                    if char_pair[0] == s[0] and char_pair[1] == s[last]:
                        s = s.lstrip(char_pair[0]).rstrip(char_pair[1])
            counter += 1
    return s


def strip_parent_name(work: str, parent: str) -> str:
    '''The work's name with the parent's name taken out.

    The legacy's primary strip: the parent's punctuation becomes
    spaces, the spaces may be filled by up to two non-letters in the
    work name, and the match is removed. A match that does not start at
    the beginning leaves an ellipsis for the text it was taken from.

    When the parent's name never matches, the legacy's common-word
    fallback runs instead: the longest run of words the two names share
    - compared after boiling - is eliminated, a run that starts mid-name
    leaving an ellipsis behind. What survives either path is cleaned of
    orphan punctuation.
    '''
    if not isinstance(parent, str):
        parent = '; '.join(parent)
    if not isinstance(work, str):
        work = '; '.join(work)

    # replace any punctuation or numbers with a space (to remove any
    # inconsistent punctuation and numbering) - (?u) specifies the
    # re.UNICODE flag in sub
    clean_parent = re.sub(r'(?u)[\W]', ' ', parent)
    # now allow the spaces to be filled with up to 2 non-letters
    pattern_parent = clean_parent.replace(" ", r'\W{0,2}')
    pattern_parent = r'(^|.*?\s)(\W*' + pattern_parent + r'\W?)(.*)'
    m = re.compile(pattern_parent, re.IGNORECASE | re.UNICODE).search(work)
    if m:
        if m.group(1):
            stripped_work = m.group(1) + "\u2026" + m.group(3)
        else:
            stripped_work = m.group(3)
        # may not have a full work name in the parent (missing op. no. etc.)
        stripped_work = stripped_work.lstrip(":;,.- ")
    else:
        stripped_work = _common_word_fallback(work, parent)
    return strip_excess_punctuation(stripped_work)


def _from_roman(s: str) -> int:
    '''The value of a roman numeral, upper or lower case.'''
    result = 0
    index = 0
    for numeral, integer in ROMAN_MAP:
        while s[index:index + len(numeral)] == numeral:
            result += integer
            index += len(numeral)
    return result


def _boil(s: str) -> str:
    '''Remove punctuation, spaces, capitals and accents, and fold the
    roman numerals to digits, for string comparisons. Ported from the
    legacy's ``boil``.
    '''
    s = s.lower()
    s = _fold_roman_numerals(s)
    s = (s.replace('sch', 'sh')
          .replace('\u00df', 'ss')
          .replace('sz', 'ss')
          .replace('\u0153', 'oe')
          .replace('oe', 'o')
          .replace('\u00fc', 'ue')
          .replace('ue', 'u')
          .replace('\u00e6', 'ae')
          .replace('ae', 'a')
          .replace('\u266f', 'sharp')
          .replace('\u266d', 'flat')
          .replace('\u2013', '-')
          .replace('\u2014', '-'))
    # strip the accents after the spellings that carry them were folded
    s = ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn')
    return re.compile(r'\W*', re.ASCII).sub('', s).strip().lower().rstrip("s'")


def _fold_roman_numerals(s: str) -> str:
    '''Replace the roman numerals - where followed by punctuation, space
    or the end - by their digits. Ported from the legacy's
    ``replace_roman_numerals``.
    '''
    for roman in RE_ROMANS.findall(s):
        if roman[0]:
            s = re.sub(r'\b' + roman[0] + r'\b', str(_from_roman(roman[0])), s)
    return s


def _longest_common_run(a: list, b: list) -> dict:
    '''The longest common sub-list of two lists of boiled words, with its
    start in the first. Ported from the legacy's
    ``longest_common_substring``.
    '''
    m = [[0] * (1 + len(b)) for i in range(1 + len(a))]
    longest, x_longest = 0, 0
    for x in range(1, 1 + len(a)):
        for y in range(1, 1 + len(b)):
            if a[x - 1] == b[y - 1]:
                m[x][y] = m[x - 1][y - 1] + 1
                if m[x][y] > longest:
                    longest, x_longest = m[x][y], x
    return {'start': x_longest - longest, 'length': longest}


def _common_word_fallback(work: str, parent: str) -> str:
    '''Eliminate the longest common run of words from the work name.

    Ported from the fallback branch of the legacy's
    ``strip_parent_from_work`` - without the synonym canonisation (see
    the module docstring) and without the empty-part annotations, which
    are option text the legacy adds elsewhere: an empty result stands,
    as the recorded tags show.
    '''
    prev_stripped = ''
    counter = 1
    stripped_work = work
    while prev_stripped != stripped_work:
        if counter > 20:
            break  # in case something went awry
        prev_stripped = stripped_work
        parent_words = WORD.finditer(parent)
        clean_parent_words = [_boil(m.group()) for m in parent_words]
        work_words = list(WORD.finditer(stripped_work))
        clean_work_words = [_boil(m.group()) for m in work_words]
        common = _longest_common_run(clean_work_words, clean_parent_words)
        seq_start = common['start']
        seq_length = common['length']
        # the original items (before cleaning)
        full_seq = work_words[seq_start:seq_start + seq_length]
        # number of words in the common run
        full_seq_length = sum(len(x.group().split()) for x in full_seq)
        if full_seq_length > 0:
            potential = stripped_work
            if seq_start > 0:
                ellipsis = ' \u2026 '
            else:
                ellipsis = ''
            if counter > 1:
                potential = potential.rstrip(' :,-\u2026')
                potential = potential.replace('(\u2026)', '').rstrip()
            potential = (potential[:work_words[seq_start].start()]
                         + ellipsis
                         + potential[work_words[seq_start + seq_length - 1].end():])
            potential = potential.lstrip(' :,-')
            potential = re.sub(r'(\W*\u2026\W*)(\W*\u2026\W*)', ' \u2026 ',
                               potential)
            potential = strip_excess_punctuation(potential)
            if full_seq_length >= MIN_COMMON_WORDS or potential == '':
                # make sure it is more than the required minimum, unless a
                # full strip results anyway
                stripped_work = potential
        counter += 1
    return stripped_work
