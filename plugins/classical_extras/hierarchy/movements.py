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
    flag as collections (``part of collection``). A fused level
    descends when ANY of its works is a collection - the legacy flags
    the whole level from any member's relation.
    """
    collections = set(collections)
    group = tuple(top_ids)
    if not group:
        return ()
    n = len(level_ids) - 1
    while group and any(work in collections for work in group):
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
    legacy's ``disc + track / 1000`` float. The pairs are sorted by it
    **within each top work** - the legacy numbers per top, so two
    interleaved works on one disc keep their own counts - a counter
    increments while the group stays the same and resets when it
    changes, and the total of a group is the highest count it reached.

    The result is a list aligned with the input - ``(number, total)``
    per track. Tracks with an empty group (no top, no hierarchy) are
    never numbered - the legacy's ``if movementgroup:`` gate - and a
    group holding a single movement is left unnumbered, as the
    legacy's total>1 gate does.
    """
    # the per-top scope: the legacy runs its counting pass once per top
    # work, so the adjacency that feeds the reset is adjacency within a
    # top's own tracks
    by_top: Dict[Tuple[str, ...], List[Tuple[int, Tuple[float, ...]]]] = {}
    for position, (sort_key, group) in enumerate(track_groups):
        by_top.setdefault(group, []).append((position, (sort_key, group)))
    numbers: List[Tuple] = [(None, None)] * len(track_groups)
    for group, members in by_top.items():
        if not group:
            # a workless track never reaches the legacy's numbering
            continue
        totals: Dict[Tuple[str, ...], int] = {}
        previous = None
        count = 0
        placement: List[Tuple[int, int]] = []
        for position, (sort_key, sub_group) in sorted(
                members, key=lambda m: m[1][0]):
            count = count + 1 if sub_group == previous else 1
            previous = sub_group
            placement.append((position, count))
            totals[sub_group] = max(totals.get(sub_group, 0), count)
        for position, number in placement:
            total = totals.get(group, 0)
            if total > 1:
                numbers[position] = (number, total)
            else:
                numbers[position] = (None, None)
    return numbers


def stripped_movement(text: str) -> str:
    """The movement text without its leading numerals - the legacy's
    five passes of the roman-numeral and integer strips."""
    movt = text
    for _ in range(0, 5):
        movt = _INT_AT_START.sub('', RE_ROMANS_AT_START.sub('', movt)).strip()
    return movt
