# -*- coding: utf-8 -*-
"""Movement numbers: which movement of its group a track is.

The legacy numbers a work's movements so a player can group and sort
them: the tracks of a top work are walked in tracknumber order, each
track's *movement group* is the deepest work-id chain that is not a
collection, a counter increments while the group stays the same and
resets when it changes, and the last count seen for a group is its
total. A group holding a single movement gets no number at all - the
legacy writes ``~cwp_movt_num``/``~cwp_movt_tot`` only when the total
exceeds one.

The movement text also gets its leading numerals stripped - ``I.
Introitus`` becomes ``Introitus`` - so the movement tag carries the
name without the number the numbering tags already hold.

All of it is plain data here: id lists in, numbers and text out.
"""

from __future__ import annotations

import re

from typing import Dict, Iterable, List, Sequence, Tuple

from .diffpair import RE_ROMANS_AT_START

# Matches positive integers with punctuation - the legacy's p2.
_INT_AT_START = re.compile(r'^\W*\d+[.):-]')


def movement_group(
        top_ids: Sequence[str],
        level_ids: Sequence[Sequence[str]],
        collections: Iterable[str]) -> Tuple[str, ...]:
    """The track's movement group: the legacy's while-is_collection walk.

    The group starts as the top work's id chain and descends one level
    each time the current work is a collection, bottoming out at the
    lowest level. ``level_ids`` holds the id lists per level, the
    lowest first with the top last (the adapter's ``levels``);
    ``collections`` is the set of work ids the MusicBrainz relations
    flag as collections (``part of collection``).
    """
    collections = set(collections)
    group = tuple(top_ids)
    if not group:
        return ()
    n = len(level_ids) - 1
    while group and group[0] in collections:
        n -= 1
        if n < 0:
            break
        if level_ids[n]:
            group = tuple(level_ids[n])
        else:
            break
    return group


def movement_numbers(
        track_groups: Sequence[Tuple[float, Tuple[str, ...]]]) -> List:
    """Number the movements of every group, in track order.

    ``track_groups`` holds ``(sort_key, group)`` pairs, one per track,
    in the caller's order. The sort key is the track's position - the
    legacy's ``disc + track / 1000`` float. The pairs are sorted by it,
    a counter increments while the group stays the same and resets when
    it changes, and the total of a group is the highest count it
    reached.

    The result is a list aligned with the input - ``(number, total)``
    per track, or ``(None, None)`` when the track's group holds a
    single movement, which the legacy leaves unnumbered.
    """
    sorted_pairs = sorted(enumerate(track_groups), key=lambda p: p[1][0])
    totals: Dict[Tuple[str, ...], int] = {}
    counts_by_position: List[int] = [0] * len(track_groups)
    previous = None
    count = 0
    for position, (tracknumber, group) in sorted_pairs:
        count = count + 1 if group == previous else 1
        previous = group
        counts_by_position[position] = count
        totals[group] = count
    result = []
    for (tracknumber, group), number in zip(track_groups, counts_by_position):
        total = totals.get(group, 0)
        if total > 1:
            result.append((number, total))
        else:
            result.append((None, None))
    return result


def stripped_movement(text: str) -> str:
    """The movement text without its leading numerals - the legacy's
    five passes of the roman-numeral and integer strips."""
    movt = text
    for _ in range(0, 5):
        movt = _INT_AT_START.sub('', RE_ROMANS_AT_START.sub('', movt)).strip()
    return movt
