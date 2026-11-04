# -*- coding: utf-8 -*-
"""The fuzzy title-vs-work match: what the track title adds.

The legacy's ``diff_pair`` decides, when a track title carries text
beyond the work name, what that extra text is - it becomes the
``{...}`` brace after the part name. Along the way it canonizes opus
numbers and keys (``op. 144`` and ``op144`` must compare equal),
boils away punctuation/case/accents, honours the user's synonyms and
replacements, ignores singleton new words in the middle of a title
(the proximity options), strips the leading movement numbers, and
gives up when a longest-common-substring test says the remaining text
is not really new.

This module is a mechanical de-classing of that code: every method is
now a pure function, and everything that was state on the processor
(the synonym tuples, the replacement tuples, the options) is
parameters. The bodies are the legacy's, with the ``write_log`` calls
removed - the reference for every rule is ``PartLevels.diff_pair`` and
its helpers.

The caller (the new engine's publish) passes:

* ``synonyms`` - the parsed synonym tuples (the legacy's
  ``self.synonyms[track]``, from ``get_text_tuples``);
* ``replacements`` - the parsed replacement tuples;
* ``options`` - the dict of the ``cwp_`` options the match reads:
  ``cwp_removewords_p``, ``cwp_proximity``, ``cwp_end_proximity``,
  ``cwp_prepositions``, ``cwp_substring_match``,
  ``cwp_split_hyphenated``.
"""

from __future__ import annotations

import re
import unicodedata

# COMMONLY USED REGEX - the legacy's constants
ROMAN_NUMERALS = (r'\b((?=[MDCLXVI])(M{0,4}(CM|CD|D?)?C{0,3}'
                  r'(XC|XL|L?)?X{0,3}(IX|IV|V?)?I{0,3}))'
                  r'(?:\.|\-|:|;|,|\s|$)')
ROMAN_NUMERALS_AT_START = r'^\W*' + ROMAN_NUMERALS
RE_ROMANS = re.compile(ROMAN_NUMERALS, re.IGNORECASE)
RE_ROMANS_AT_START = re.compile(ROMAN_NUMERALS_AT_START, re.IGNORECASE)
RE_NOTES = r'(\b[ABCDEFG])'
RE_ACCENTS = (r'(\-sharp(?:\s+|\b)|\-flat(?:\s+|\b)|\ssharp(?:\s+|\b)'
              r'|\sflat(?:\s+|\b)|\u266F(?:\s+|\b)|\u266D(?:\s+|\b)'
              r'|(?:[:,.]?\s+|$|\-))')
RE_SCALES = r'(major|minor)?(?:\b|$)'
RE_KEYS = re.compile(RE_NOTES + RE_ACCENTS + RE_SCALES,
                     re.UNICODE | re.IGNORECASE)


def from_roman(s):
    romanNumeralMap = (('M', 1000),
                       ('CM', 900),
                       ('D', 500),
                       ('CD', 400),
                       ('C', 100),
                       ('XC', 90),
                       ('L', 50),
                       ('XL', 40),
                       ('X', 10),
                       ('IX', 9),
                       ('V', 5),
                       ('IV', 4),
                       ('I', 1))
    result = 0
    index = 0
    for numeral, value in romanNumeralMap:
        while s[index:index + len(numeral)] == numeral:
            result += value
            index += len(numeral)
    return result


def replace_roman_numerals(s):
    """Replaces roman numerals include in s, where followed by certain
    punctuation, by digits"""
    romans = RE_ROMANS.findall(s)
    for roman in romans:
        if roman[0]:
            numerals = str(roman[0])
            digits = str(from_roman(numerals))
            to_replace = r'\b' + roman[0] + r'\b'
            s = re.sub(to_replace, digits, s)
    return s


def longest_common_substring(s1, s2):
    """
    Standard lcs algo for short strings, or if suffix tree does not work
    :param s1: substring 1
    :param s2: substring 2
    :return: {'string': the longest common substring,
        'start': the start position in s1,
        'length': the length of the common substring}
    NB this also works on list arguments - i.e. it will find the
    longest common sub-list
    """
    m = [[0] * (1 + len(s2)) for i in range(1 + len(s1))]
    longest, x_longest = 0, 0
    for x in range(1, 1 + len(s1)):
        for y in range(1, 1 + len(s2)):
            if s1[x - 1] == s2[y - 1]:
                m[x][y] = m[x - 1][y - 1] + 1
                if m[x][y] > longest:
                    longest = m[x][y]
                    x_longest = x
            else:
                m[x][y] = 0
    return {'string': s1[x_longest - longest: x_longest],
            'start': x_longest - longest, 'length': longest}


def strip_excess_punctuation(s):
    """
    remove orphan punctuation, unmatched quotes and brackets
    :param s: string
    :return: string
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
            s = s.lstrip(u"\u2019")  # Right single quote
            s = s.lstrip(u"\u201D")  # Right double quote
            if s.count(u"\u201E") == 0:  # u201E is lower double quote (German etc.)
                s = s.rstrip(u"\u201C")  # Left double quote - only strip if there is no German-style lower quote present
            s = s.rstrip(u"\u2018")  # Left single quote
            if s.count('"') % 2 != 0:
                s = s.strip('"')
            if s.count("'") % 2 != 0:
                s = s.strip("'")
            if len(s) > 0 and s[0] == u"\u201C" and s.count(u"\u201D") == 0:
                s = s.lstrip(u"\u201C")
            if len(s) > 0 and s[-1] == u"\u201D" and s.count(u"\u201C") == 0 and s.count(u"\u201E") == 0:  # only strip if there is no German-style lower quote present
                s = s.rstrip(u"\u201D")
            if len(s) > 0 and s[0] == u"\u2018" and s.count(u"\u2019") == 0:
                s = s.lstrip(u"\u2018")
            if len(s) > 0 and s[-1] == u"\u2019" and s.count(u"\u2018") == 0:
                s = s.rstrip(u"\u2019")
            if s:
                if s.count("\"") == 1:
                    s = s.replace('"', '')
                if s.count("\'") == 1:
                    s = s.replace(" \'", " ")
                if "(" in s and ")" not in s:
                    s = s.replace("(", "")
                if ")" in s and "(" not in s:
                    s = s.replace(")", "")
                match_chars = [("(", ")"), ("[", "]"), ("{", "}")]
                last = len(s) - 1
                for char_pair in match_chars:
                    if char_pair[0] == s[0] and char_pair[1] == s[last]:
                        s = s.lstrip(char_pair[0]).rstrip(char_pair[1])
            counter += 1
    return s


def boil(s):
    """
    Remove punctuation, spaces, capitals and accents for string
    comparisons
    """
    s = s.lower()
    s = replace_roman_numerals(s)
    s = s.replace('sch', 'sh')\
        .replace(u'\xdf', 'ss')\
        .replace('sz', 'ss')\
        .replace(u'\u0153', 'oe')\
        .replace('oe', 'o')\
        .replace(u'\u00fc', 'ue')\
        .replace('ue', 'u')\
        .replace(u'\u00e6', 'ae')\
        .replace('ae', 'a')\
        .replace(u'\u266F', 'sharp')\
        .replace(u'\u266D', 'flat')\
        .replace(u'\u2013', '-')\
        .replace(u'\u2014', '-')
    # first term above is to remove the markers used for synonyms, to
    # enable a true comparison
    punc = re.compile(r'\W*', re.ASCII)
    s = ''.join(
        c for c in unicodedata.normalize(
            'NFD',
            s) if unicodedata.category(c) != 'Mn')
    boiled = punc.sub('', s).strip().lower().rstrip("s'")
    return boiled


def stencil(matches_tuple, test_string):
    """
    Produce lists of matching items, AND the items in between, in equal
    length lists
    :param matches_tuple: tuple of regex matches
    :param test_string: original string used in regex
    :return: 'match list' - list of matched strings, 'gap list' - list
    of strings in gaps between matches
    """
    match_items = []
    gap_items = []
    dummy = False
    pointer = 0
    for match_num, match in enumerate(matches_tuple):
        start = match.start()
        end = match.end()
        if start > pointer:
            if pointer == 0:
                # add a null word item at start to keep the lists the
                # same length
                match_items.append('')
                dummy = True
            gap_items.append(test_string[pointer:start])
        else:
            if pointer > 0:
                # shouldn't happen, but just in case there are two word
                # items with no gap
                gap_items.append('')
        match_items.append(test_string[start:end])
        pointer = end
        if match_num + 1 == len(matches_tuple):
            # pick up any punc items at end
            gap_items.append(test_string[pointer:])
    return {
        'match list': match_items,
        'gap list': gap_items,
        'dummy': dummy}


def canonize_opus(s):
    """
    make opus numbers etc. into one-word items
    Canonize catalogue & opus numbers (e.g. turn K. 126 into K126 or K
    345a into K345a or op. 144 into op144):
    """
    regex = re.compile(
        r'\b((?:op|no|k|kk|kv|L|B|Hob|S|D|M)|\w+WV)\W?\s?(\d+\-?\u2013?\u2014?\d*\w*)\b',
        re.IGNORECASE)
    regex_match = regex.search(s)
    s_canon = s
    if regex_match and len(regex_match.groups()) == 2:
        pt1 = regex_match.group(1) or ''
        pt2 = regex_match.group(2) or ''
        if regex_match.group(1) and regex_match.group(2):
            pt1 = re.sub(
                r'^\W*no\b',
                '',
                regex_match.group(1),
                flags=re.IGNORECASE)
        s_canon = pt1 + pt2
    return s_canon


def canonize_key(s):
    """
    make keys into standardized one-word items
    """
    match = RE_KEYS.search(s)
    s_canon = s
    if match:
        if match.group(2):
            k2 = re.sub(
                r'\-sharp|\u266F',
                'sharp',
                match.group(2),
                flags=re.IGNORECASE)
            k2 = re.sub(r'\-flat|\u266D', 'flat', k2, flags=re.IGNORECASE)
            k2 = k2.replace('-', '')
        else:
            k2 = ''
        if not match.group(3) or match.group(
                3).strip() == '':  # if the scale is not given, assume it is the major key
            if match.group(1).isupper(
            ) or k2 != '':  # but only if it is upper case or has an accent
                k3 = 'major'
            else:
                k3 = ''
        else:
            k3 = match.group(3).strip()
        s_canon = match.group(1).strip() + k2.strip() + k3
    return s_canon


def canonize_synonyms(tuples, s):
    """
    make synonyms equal
    :param tuples
    :param s: A string
    """
    s_canon = s
    syn_patterns = []
    syn_subs = []
    for syn_tup in tuples:
        syn_pattern = r'((?:^|\W)' + \
            r'(?:$|\W)|(?:^|\W)'.join(syn_tup) + r'(?:$|\W))'
        syn_patterns.append(syn_pattern)
        # to get the last synonym in the tuple - the canonical form
        syn_sub = syn_tup[-1:][0]
        syn_subs.append(syn_sub)
    for syn_ind, pattern in enumerate(syn_patterns):
        regex = re.compile(pattern, re.IGNORECASE)
        regex_match = regex.search(s)
        if regex_match:
            test_reg = regex_match.group().strip()
            s_canon = s_canon.replace(test_reg, syn_subs[syn_ind])
    return s_canon


def find_synonyms(synonyms, reg_item):
    """
    extend regex item to include synonyms
    :param synonyms: the synonym tuples
    :param reg_item: A regex portion
    :return: reg_new: A replacement for reg_item that includes all its
    synonyms (if reg_item matches the last in a synonym tuple)
    """
    syn_others = []
    syn_all = []
    for syn_tup in synonyms:
        # to get the last synonym in the tuple - the canonical form
        syn_last = syn_tup[-1:][0]
        if re.match(r'^\s*' + reg_item + r'\s*$', syn_last, re.IGNORECASE):
            syn_others += syn_tup[:-1]
            syn_all += syn_tup
    if syn_others:
        reg_item = '(?:' + ')|(?:'.join(syn_others) + \
            ')|(?:' + reg_item + ')'
    return reg_item, syn_all


def listify(synonyms, split_hyphenated, s):
    """
    Turn a string into a list of 'words', where words may also be
    phrases which are then 'canonized' - i.e. turned into equivalents
    for comparison purposes
    :param synonyms: the synonym tuples
    :param split_hyphenated: the cwp_split_hyphenated option
    :param s: string
    :return: s_tuple: a tuple of all the **match objects** (re words and
    defined phrases)
             s_test_tuple: a tuple of the matched and canonized words
             and phrases (i.e. a tuple of strings, not objects)
    """
    tuples = synonyms
    # just list anything that is a synonym (with word boundary markers)
    syn_pattern = '|'.join(
        [r'(?:^|\W|\b)' + x + r'(?:$|\W)' for y in synonyms for x in y])
    op = find_synonyms(synonyms, r'(?:op|no|k|kk|kv|L|B|Hob|S|D|M|\w+WV)')
    op_groups = op[0]
    op_all = op[1]
    notes = find_synonyms(synonyms, r'[ABCDEFG]')
    notes_groups = notes[0]
    notes_all = notes[1]
    sharp = find_synonyms(synonyms, r'sharp')
    sharp_groups = sharp[0]
    sharp_all = sharp[1]
    flat = find_synonyms(synonyms, r'flat')
    flat_groups = flat[0]
    flat_all = flat[1]
    major = find_synonyms(synonyms, r'major')
    major_groups = major[0]
    major_all = major[1]
    minor = find_synonyms(synonyms, r'minor')
    minor_groups = minor[0]
    minor_all = minor[1]
    opus_pattern = r"(?:\b((?:(" + op_groups + \
        r"))\W?\s?\d+\-?\u2013?\u2014?\d*\w*)\b)"
    note_pattern = r"(\b" + notes_groups + r")"
    accent_pattern = r"(?:\-(" + sharp_groups + r")(?:\s+|\b)|\-(" + flat_groups + r")(?:\s+|\b)|\s(" + sharp_groups + \
                     r")(?:\s+|\b)|\s(" + flat_groups + r")(?:\s+|\b)|\u266F(?:\s+|\b)|\u266D(?:\s+|\b)|(?:[:,.]?\s+|$|\-))"
    scale_pattern = r"(?:((" + major_groups + \
        r")|(" + minor_groups + r"))?\b)"
    key_pattern = note_pattern + accent_pattern + scale_pattern
    hyphen_split_pattern = r"(?:\b|\"|\')(\w+['’]?\w*)|(?:\b\w+\b)|(\B\&\B)"
    # treat em-dash and en-dash as hyphens
    hyphen_embed_pattern = (r"(?:\b|\"|\')(\w+['’\-\u2013\u2014]?\w*)"
                            r"|(?:\b\w+\b)|(\B\&\B)")

    # The regex is split into two iterations as putting it all together
    # can have unpredictable consequences
    # - may match synonyms before op's even though that is later in the
    # string

    # First match the op's and keys
    regex_1 = opus_pattern + r"|(" + key_pattern + r")"
    matches_1 = re.finditer(regex_1, s, re.UNICODE | re.IGNORECASE)
    s_list = []
    s_test_list = []
    s_scrubbed = s
    all_synonyms_lists = [
        op_all,
        notes_all,
        sharp_all,
        flat_all,
        sharp_all,
        flat_all,
        major_all,
        minor_all]
    matches_list = [2, 4, 5, 6, 7, 8, 10, 11]
    for match in matches_1:
        test_a = match.group()
        match_a = []
        match_a.append(match.group())
        for j in range(1, 12):
            match_a.append(match.group(j))
        # 0. overall match
        # 1. overall opus match
        # 2. 2-char op match
        # 3. overall key match
        # 4. note match
        # 5. hyphenated sharp match
        # 6. hyphenated flat match
        # 7. non-hyphenated sharp match
        # 8. non-hyphenated flat match
        # 9. overall scale match
        # 10. major match
        # 11. minor match
        for i, all_synonyms_list in enumerate(all_synonyms_lists):
            if all_synonyms_list and match_a[matches_list[i]]:
                match_regex = [re.match(pattern, match_a[matches_list[i]], re.IGNORECASE).group()
                               for pattern in all_synonyms_list
                               if re.match(pattern, match_a[matches_list[i]], re.IGNORECASE)]
                if match_regex:
                    match_a[matches_list[i]] = canonize_synonyms(
                        tuples, match_a[matches_list[i]])
                    test_a = re.sub(r"\b" + match_regex[0] + r"(?:\b|$|\s|\.)",
                                    match_a[matches_list[i]],
                                    test_a, flags=re.IGNORECASE)
        if match_a[1]:
            clean_opus = test_a.strip(' ,.:;/-?"')
            test_a = re.sub(
                re.escape(clean_opus),
                canonize_opus(clean_opus),
                test_a,
                flags=re.IGNORECASE)
        if match_a[3]:
            clean_key = test_a.strip(' ,.:;/-?"')
            test_a = re.sub(
                re.escape(clean_key),
                canonize_key(clean_key),
                test_a,
                flags=re.IGNORECASE)

        s_test_list.append(test_a)
        s_list.append(match)
        s_scrubbed_list = list(s_scrubbed)
        for char in range(match.start(), match.end()):
            if len(s_scrubbed_list) >= match.end():  # belt and braces
                s_scrubbed_list[char] = '#'
        s_scrubbed = ''.join(s_scrubbed_list)

    # Then match the synonyms and remaining words
    if split_hyphenated:
        regex_2 = r"(" + syn_pattern + r")|" + hyphen_split_pattern
        # allow ampersands and non-latin characters as word characters.
        # Treat apostrophes as part of words.
        # Treat opus and catalogue entries - e.g. K. 657 or OP.5 or
        # op. 35a or CD 144 or BWV 243a - as one word
        # also treat ranges of opus numbers (connected by dash, en dash
        # or em dash) as one word
    else:
        regex_2 = r"(" + syn_pattern + r")|" + hyphen_embed_pattern
        # as previous but also treat embedded hyphens as part of words.
    matches_2 = re.finditer(
        regex_2, s_scrubbed, re.UNICODE | re.IGNORECASE)
    for match in matches_2:
        if match.group(1) and match.group(1) == match.group():
            s_test_list.append(
                canonize_synonyms(tuples, match.group(1)))  # synonym
        else:
            s_test_list.append(match.group())
        s_list.append(match)
    if s_list:
        s_zip = list(zip(s_list, s_test_list))
        s_list, s_test_list = zip(
            *sorted(s_zip, key=lambda tup: tup[0].start()))
    s_tuple = tuple(s_list)
    s_test_tuple = tuple(s_test_list)
    return {'s_tuple': s_tuple, 's_test_tuple': s_test_tuple}


def diff_pair(mb_item,
              title_item,
              synonyms,
              replacements,
              options,
              remove_numbers=True):
    """
    Removes common text (or synonyms) from title item
    :param mb_item: the work name
    :param title_item: the track title
    :param synonyms: the parsed synonym tuples
    :param replacements: the parsed replacement tuples
    :param options: the cwp_ options the match reads
    :param remove_numbers: remove movement numbers when comparing
    :return: Reduced title item
    """
    mb = mb_item.strip()
    if not mb:
        return None
    ti = title_item.strip(" :;-.,")
    if ti.count('"') == 1:
        ti = ti.strip('"')
    if ti.count("'") == 1:
        ti = ti.strip("'")
    if not ti:
        return None

    if options["cwp_removewords_p"]:
        removewords = options["cwp_removewords_p"].split(',')
    else:
        removewords = []
    # remove numbers, roman numerals, part etc and punctuation from the
    # start
    found_prefix = True
    i = 0
    while found_prefix:
        if i > 20:
            break  # safety valve
        found_prefix = False
        for prefix in removewords:
            if prefix[0] != " ":
                prefix2 = str(prefix).lower().lstrip()
                if mb.lower().startswith(prefix2):
                    found_prefix = True
                    mb = mb[len(prefix2):]
                if ti.lower().startswith(prefix2):
                    found_prefix = True
                    ti = ti[len(prefix2):]
        mb = mb.strip()
        ti = ti.strip()
        i += 1

    #  replacements
    for tup in replacements:
        for ind in range(0, len(tup) - 1):
            ti = re.sub(tup[ind], tup[-1], ti, flags=re.IGNORECASE)

    ti_tuples = listify(synonyms, options["cwp_split_hyphenated"], ti)
    ti_tuple = ti_tuples['s_tuple']
    ti_test_tuple = ti_tuples['s_test_tuple']

    mb_tuples = listify(synonyms, options["cwp_split_hyphenated"], mb)
    mb_test_tuple = mb_tuples['s_test_tuple']

    ti_stencil = stencil(ti_tuple, ti)
    ti_list = ti_stencil['match list']
    ti_list_punc = ti_stencil['gap list']
    ti_test_list = list(ti_test_tuple)
    if ti_stencil['dummy']:
        # to deal with case where stencil has added a dummy item at the
        # start
        ti_test_list.insert(0, '')
    # zip is an iterable, not a list in Python 3, so make it re-usable
    ti_zip_list = list(zip(ti_list, ti_list_punc))

    # len(ti_list) should be = len(ti_test_list) as only difference
    # should be synonyms which are each one 'word'
    # However, because of the grouping of some words via regex, it is
    # possible that inconsistencies might arise - the legacy logs an
    # error here and continues
    mb_list2 = list(mb_test_tuple)
    for index, mb_bit2 in enumerate(mb_list2):
        mb_list2[index] = boil(mb_bit2)
    ti_new = []
    ti_rich_list = []
    for i, ti_bit_test in enumerate(ti_test_list):
        if i <= len(ti_zip_list) - 1:
            ti_bit = ti_zip_list[i]
            # NB ti_bit is a tuple where the word (1st item) is grouped
            # with its following punctuation (2nd item)
        else:
            ti_bit = ('', '')
        ti_rich_list.append((ti_bit, True))
        # Boolean to indicate whether ti_bit is a new word

        if ti_bit_test == '':
            ti_rich_list[i] = (ti_bit, False)
        else:
            if boil(ti_bit_test) in mb_list2:
                ti_rich_list[i] = (ti_bit, False)

    if remove_numbers:  # Only remove numbers at the start if they are not new items
        p0 = re.compile(r'\b\w+\b')
        p1 = RE_ROMANS
        p2 = re.compile(r'^\d+')  # Matches positive integers
        starts_with_numeral = True
        while starts_with_numeral:
            starts_with_numeral = False
            if ti_rich_list and p0.match(ti_rich_list[0][0][0]):
                start_word = p0.match(ti_rich_list[0][0][0]).group()
                if p1.match(start_word) or p2.match(start_word):
                    if not ti_rich_list[0][1]:
                        starts_with_numeral = True
                        ti_rich_list.pop(0)
                        ti_test_list.pop(0)

    s = 0
    index = 0
    change = ()
    for i, (t, n) in enumerate(ti_rich_list):
        if n:
            s += 1
            index = i
            change = t  # NB this is a tuple

    p = options["cwp_proximity"]
    ep = options["cwp_end_proximity"]
    # NB these may be modified later

    if s == 1:
        if 0 < index < len(ti_rich_list) - 1:
            # ignore singleton new words in middle of title unless they
            # are within "cwp_end_proximity" from the start or end
            # also make sure that the item is just one word before
            # eliminating
            if ep < index < len(ti_rich_list) - ep - \
                    1 and len(change[0].split()) == 1:
                ti_rich_list[index] = (change, False)
                s = 0

    # remove prepositions
    if options["cwp_prepositions"]:
        prepositions_fat = options["cwp_prepositions"].split(
            ',')
        prepositions = [w.strip() for w in prepositions_fat]
        for i, ti_bit_test in enumerate(
                reversed(ti_test_list)):  # Need to reverse it to check later prepositions first
            if ti_bit_test.lower().strip() in prepositions:
                # NB i is counting up while traversing the list backwards
                j = len(ti_rich_list) - i - 1
                if i == 0 or not ti_rich_list[j + 1][1]:
                    # Don't make it false if it is preceded by a
                    # non-preposition new word
                    prev_new = j > 0 and ti_rich_list[j - 1][1]
                    prev_prep = (ti_test_list[j - 1].lower()
                                 not in prepositions)
                    if not (prev_new and prev_prep):
                        ti_rich_list[j] = (ti_rich_list[j][0], False)

    # create comparison for later usage
    compare_string = ''
    for item in ti_rich_list:
        if item[1]:
            compare_string += item[0][0]
    ti_compare = boil(compare_string)
    compare_length = len(ti_compare)

    if s > 0:
        d = p - ep
        start = True  # To keep track of new words at the start of the title
        for i, (ti_bit, new) in enumerate(ti_rich_list):
            if not new:
                if start:
                    prox_test = ep
                else:
                    prox_test = p
                if prox_test > 0:
                    for j in range(0, prox_test + 1):
                        if i + j < len(ti_rich_list):
                            if ti_rich_list[i + j][1]:
                                ti_rich_list[i] = (ti_bit, True)
                        else:
                            if j <= p - d:
                                ti_rich_list[i] = (ti_bit, True)
            else:
                p = options["cwp_proximity"]
                start = False
            if not ti_rich_list[i][1]:
                p -= 1
                ep -= 1
    nothing_new = True
    for (ti_bit, new) in ti_rich_list:
        if new:
            nothing_new = False
            new_prev = True
            break
    if nothing_new:
        return None
    else:
        new_prev = False
        for i, (ti_bit, new) in enumerate(ti_rich_list):
            if new:
                if not new_prev:
                    if i > 0:
                        # check to see if the last char of the prev
                        # punctuation group needs to be added first
                        if len(ti_rich_list[i - 1][0][1]) > 1:
                            # i.e. ti_bit[1][-1] of previous loop
                            ti_new.append(ti_rich_list[i - 1][0][1][-1])
                ti_new.append(ti_bit[0])
                if len(ti_bit[1]) > 1:
                    if i < len(ti_rich_list) - 1:
                        if ti_rich_list[i + 1][1]:
                            ti_new.append(ti_bit[1])
                        else:
                            ti_new.append(ti_bit[1][:-1])
                    else:
                        ti_new.append(ti_bit[1])
                else:
                    ti_new.append(ti_bit[1])
            else:
                if new != new_prev:
                    ti_new.append(u"\u2026" + ' ')

            new_prev = new
    if ti_new:
        ti = ''.join(ti_new)
    else:
        return None
    # see if there is any significant difference between the strings
    if ti:
        nopunc_ti = ti_compare
        nopunc_mb = boil(mb)
        # ti_len = len(nopunc_ti) use compare_length instead (= len
        # before removals and additions)
        substring_proportion = float(options["cwp_substring_match"]) / 100
        sub_len = compare_length * substring_proportion
        if substring_proportion < 1:
            lcs = longest_common_substring(nopunc_mb, nopunc_ti)['string']
            if len(lcs) >= sub_len:
                return None
    # remove duplicate successive words (and remove first word of title
    # item if it duplicates last word of mb item)
    if ti:
        ti_list_new = re.split(' ', ti)
        ti_list_ref = ti_list_new
        ti_bit_prev = None
        for i, ti_bit in enumerate(ti_list_ref):
            if ti_bit != "...":
                if i > 1:
                    if boil(ti_bit) == boil(ti_bit_prev):
                        ti_list_new.pop(i)
                ti_bit_prev = ti_bit
        if ti_list_new and mb_list2:
            if boil(ti_list_new[0]) == mb_list2[-1]:
                ti_list_new.pop(0)
        if len(ti_list_new) > 0:
            ti = ' '.join(ti_list_new)
        else:
            return None
    else:
        return None
    ti = strip_excess_punctuation(ti)
    if ti and len(ti) > 0:
        return ti
    else:
        return None
