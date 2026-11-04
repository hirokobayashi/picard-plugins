# -*- coding: utf-8 -*-
"""Destination tags: what the user's work and part tags carry.

The adapter writes the hierarchy tags (``~cwp_work_N``, ``~cwp_part_N``,
``~cwp_work_top``, ``~cwp_work_group``) - the shape of the work tree on
the track. The user's tags need a different cut of that tree: one
*work* name (the reference level's), one *part* name (level 0's), a
*groupheading* (the levels above the reference), and the *extended*
variants that carry the multi-level joins. The legacy derives these in
``extend_metadata`` from the levels it embellished; this module derives
them from the adapter's level names by the same structural rules.

The rules, read off the legacy:

* ``work`` is the reference level's name. On a single-work album the
  reference sits one below the levels shown, and the work is the
  album's own work.
* ``groupheading`` is the level above the reference (level 1 on a
  multi-level album, the reference itself on a single-work album);
  with more than one level above, the intermediate parts join it with
  ``':: '``.
* ``part`` is level 0's part name - or the level-0 work name when the
  album has no part levels.
* the ``~cwp_title_part_0`` title text falls back to the track title,
  and the titles style reads it when the level's title is absent.

What the legacy adds that this module does not: the ``{...}`` brace
append when the track title carries text beyond the part name (the
``diff_pair`` fuzzy match - its own phase), the arrangement/partial/
medley name prefixes, and the level-0-derived ``~cwp_X0_*`` variants.
The function is free of any Picard dependency: plain strings in, a
plain dict out.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

# The intermediate tags the derivation fills. The publish path maps
# them to the user's chosen tag names afterwards; the three display
# styles read different subsets, so all are written and the style
# picks.
_INTERMEDIATES = (
    '~cwp_part',
    '~cwp_work',
    '~cwp_groupheading',
    '~cwp_inter_work',
    '~cwp_title_work',
    '~cwp_extended_part',
    '~cwp_extended_work',
    '~cwp_extended_groupheading',
    '~cwp_extended_inter_work',
)


def destination_tags(
        level_names: Sequence[Sequence[str]],
        part_names: Sequence[Sequence[str]],
        part_levels: int,
        ref_level: int,
        single_work_album: int,
        title_parts: Sequence,
        title: str,
        multi_sep: str = ':',
        single_sep: str = ':') -> Dict[str, str]:
    """Derive the destination intermediates from the level names.

    ``level_names`` is the adapter's per-level work names (level 0
    first); ``part_names`` the per-level part names. ``ref_level`` is
    the reference level - ``part_levels - single_work_album`` on a
    multi-level album, else the level count. ``title_parts`` holds the
    per-level title text (``~cwp_title_part_N``; ``None`` where the
    title carries none). ``title`` is the track title, the fallback
    the legacy reads at level 0. The separators are the user's
    configured ones.

    The returned dict holds the intermediates the publish path and the
    display styles read. The diff trim is not applied: the part name
    carries no ``{...}`` brace yet.
    """
    works: List[str] = []
    for level in range(0, part_levels):
        works.append(_first(level_names[level]))
    works.append(_first(level_names[part_levels]))
    parts: List[str] = []
    for level in range(0, part_levels):
        parts.append(_first(part_names[level]))

    if part_levels > 0:
        groupheading = works[1]
        work_main = works[ref_level]
        work_titles = None
        inter_work = None
        if ref_level > 1:
            # the legacy's loop ascends and prepends: the HIGHER part
            # ends up first, so part[ref_level-1]: ... : part[1]
            inter_work = ''
            for r in range(1, ref_level):
                inter_work = parts[r] + (': ' + inter_work if inter_work
                                         else '')
            groupheading = (works[ref_level] + ':: ' + inter_work)
    else:
        groupheading = works[0]
        work_main = groupheading
        work_titles = None
        inter_work = None

    # the legacy's part selection tests the list's truthiness: with no
    # part levels the list is empty and the part falls back to the
    # level-0 work name
    if part_levels > 0 and parts:
        part_main = parts[0]
    else:
        part_main = works[0]

    # the title text at level 0: the level's own title part, else the
    # track title - the legacy's '~cwp_title_part_0 or ~cwp_title or
    # title' chain
    title_level0 = _first(title_parts[:1]) or title or ''

    destinations: Dict[str, str] = {tag: '' for tag in _INTERMEDIATES}
    destinations['~cwp_part'] = part_main
    destinations['~cwp_groupheading'] = groupheading
    destinations['~cwp_work'] = work_main
    destinations['~cwp_inter_work'] = inter_work or ''
    destinations['~cwp_title_work'] = work_titles or ''
    # the extended style: without the title-depth diff the extended
    # names are the works-style ones - the joins are what the legacy
    # computes when the titles carry nothing new
    destinations['~cwp_extended_part'] = part_main
    destinations['~cwp_extended_work'] = work_main
    destinations['~cwp_extended_groupheading'] = groupheading
    destinations['~cwp_extended_inter_work'] = inter_work or ''
    if part_levels == 0:
        # the legacy deletes the groupheading when there are no part
        # levels: the work is the part, and a heading would double it
        destinations['~cwp_extended_groupheading'] = ''
        destinations['~cwp_groupheading'] = ''
    # the title intermediates the titles style reads
    destinations['~cwp_title_part_0'] = title_level0
    return destinations


def _first(names: Sequence[str]) -> str:
    """The first non-empty name of a level's names, as the legacy's
    ``part.append(tm['~cwp_part_' + str(level)])`` reads the stored
    single value."""
    for name in names or ():
        if name:
            return name
    return ''
