# -*- coding: utf-8 -*-
"""Title-derived works for the tracks the hierarchy cannot reach.

A track whose recording names no work - or names one with no place in
the tree - has no hierarchy to tag it. The legacy's answer for these
orphans is to parse the title: the last ``: `` boundaries split off the
movement, and with more boundaries the middle pieces become the
inter-work and the work. How many pieces are taken depends on what the
track knows: a track with work tags (``~cwp_part_levels`` present)
splits by its part levels, a track with none splits from the right.

The legacy only uses this when the user's
``cwp_derive_works_from_title`` option is set.
"""

from __future__ import annotations

from typing import Optional, Tuple


def derive_from_title(
        title: str,
        part_levels: Optional[int],
        work_part_levels: Optional[int]) -> Tuple[str, str, Optional[str]]:
    """Split a title into ``(work, movement, inter_work)``.

    ``part_levels``/``work_part_levels`` are the track's own level
    counts, or ``None`` when the track has no work tags at all. With
    work tags and a work with movements (``work_part_levels > 0``) the
    split point depends on whether the part levels can account for
    every colon: when they can, the work is everything before the LAST
    colon (the deepest work name carries the tail's movements); when
    they cannot, only the first colon is split. Without work tags the
    last colon is split and a second one makes the middle an
    inter-work. No colon - the title is the movement, the work empty.
    """
    movt = title
    work = ""
    inter_work = None
    colons = title.count(": ")
    if part_levels is not None:
        part_levels = int(part_levels)
        work_part_levels = int(work_part_levels or 0)
        if work_part_levels > 0:  # we have a work with movements
            if colons > 0:
                title_split = title.split(': ', 1)
                title_rsplit = title.rsplit(': ', 1)
                if part_levels >= colons:
                    work = title_rsplit[0]
                    movt = title_rsplit[1]
                else:
                    work = title_split[0]
                    movt = title_split[1]
    else:
        # No works found so try and just get parts from title
        if colons > 0:
            title_split = title.rsplit(': ', 1)
            work = title_split[0]
            if colons > 1:
                colon_ind = work.rfind(':')
                inter_work = work[colon_ind + 1:].strip()
                work = work[:colon_ind]
            movt = title_split[1]
    return work, movt, inter_work
