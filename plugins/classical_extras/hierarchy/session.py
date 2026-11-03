# -*- coding: utf-8 -*-
"""Recording session/place/date tags, derived from MusicBrainz relations.

This module is part of the Picard-independent hierarchy package: it performs
no web lookups and writes no tags. The caller fetches a recording's
relations (``/recording/<id>?inc=place-rels+artist-rels``) and hands them to
:func:`recording_session_tags`; the returned dict maps tag names to values
for the caller to write.
"""

# Spaced ASCII hyphen used for recording date ranges.
_RECORDING_DATE_RANGE_SEP = ' - '

# Artist relationship types whose begin/end dates may feed the recording-date
# fallback when no "recorded at" place relationship is present. Conductor and
# orchestra are preferred (listed first); performer is a last resort.
_RECORDING_DATE_FALLBACK_TYPES = (
    'conductor', 'performing orchestra', 'orchestra', 'ensemble', 'performer')


def blank_if_none(val):
    """
    Make NoneTypes strings
    :param val: str or None
    :return: str
    """
    if not val:
        return ''
    else:
        return val


def _format_recording_date(begin, end):
    """Format a recording date span as ISO 8601.

    Preserves MusicBrainz partial precision (YYYY, YYYY-MM, YYYY-MM-DD).
    Returns a single date when begin == end (or only begin is present); a
    spaced-hyphen range "<begin> - <end>" otherwise. Equal begin/end at any
    precision collapse to a single value (the precision-match rule).
    """
    begin = blank_if_none(begin)
    end = blank_if_none(end)
    if begin and end and begin != end:
        return begin + _RECORDING_DATE_RANGE_SEP + end
    return begin or end


def recording_session_tags(relations, name_overrides=None):
    """Derive recording place/date tags from a recording's relationships.

    Takes the ``relations`` list exactly as returned by the MusicBrainz web
    service for ``/recording/<id>?inc=place-rels+artist-rels`` and returns a
    dict of tag values:

      * ``recordingsessions``: one ``"<Venue>, <City> (<date>)"`` per session
        (or ``"(<date>)"`` for a date-only fallback session), sorted
        chronologically by begin date.
      * ``recordingplace``: unique canonical place names (order preserved).
      * ``recordingcity``: unique place area names (order preserved).
      * ``recordingdate``: aggregate span across all sessions,
        ``<min-begin> - <max-end>`` (single date if the span is one day).

    Place and date are paired per "recorded at" relationship (the dates live on
    each place relationship), never as independent lists. When no recorded-at
    place exists, dated conductor/orchestra/performer relationships supply a
    single date-only fallback session, but only for dates not already covered
    by a place session.

    This is a pure function: it performs no web lookups and writes no tags.

    # TODO recordingcountry: needs area-hierarchy lookups (deferred).
    # TODO target-credit: use the relation's credited-as venue name in the
    # display tag when a real fixture with a populated place target-credit
    # exists; v1 uses the canonical place.name everywhere.
    """
    place_sessions = []  # list of (begin, end, venue, city)
    venues = []
    cities = []
    seen_venues = set()
    seen_cities = set()

    for rel in relations:
        if rel.get('target-type') != 'place':
            continue
        if rel.get('type') != 'recorded at':
            continue
        place = rel.get('place') or {}
        venue = place.get('name', '')
        area = place.get('area') or {}
        city = area.get('name', '')
        # Optionally substitute a romanized/localized name for a non-Latin
        # venue or city, keyed by the entity's MusicBrainz id (see
        # crr_romanize_place; overrides resolved asynchronously in
        # recording_process). Keyed by id so the same-named canonical value in
        # the filter and display tags stay consistent.
        if name_overrides:
            if place.get('id') in name_overrides:
                venue = name_overrides[place.get('id')]
            if area.get('id') in name_overrides:
                city = name_overrides[area.get('id')]
        # v1: always canonical place.name for the filter tag; the display tag
        # also uses canonical (target-credit deferred, see TODO above).
        begin = rel.get('begin', '')
        end = rel.get('end', '')
        place_sessions.append((begin, end, venue, city))
        if venue and venue not in seen_venues:
            seen_venues.add(venue)
            venues.append(venue)
        if city and city not in seen_cities:
            seen_cities.add(city)
            cities.append(city)

    place_sessions.sort(key=lambda s: s[0] or '')

    sessions = []
    for begin, end, venue, city in place_sessions:
        date = _format_recording_date(begin, end)
        # Join only the non-empty parts so a venue with no city doesn't leave a
        # stray ", " (and vice versa).
        where = ", ".join(p for p in (venue, city) if p)
        if date:
            label = "%s (%s)" % (where, date) if where else "(%s)" % date
        else:
            label = where
        sessions.append(label)

    # Date-only fallback from dated artist relationships, emitted only for
    # dates not already covered by the place sessions.
    fallback_begin = ''
    fallback_end = ''
    for rel in relations:
        if rel.get('target-type') != 'artist':
            continue
        if rel.get('type') not in _RECORDING_DATE_FALLBACK_TYPES:
            continue
        begin = rel.get('begin', '')
        end = rel.get('end', '')
        if not begin and not end:
            continue
        if not fallback_begin or (begin and begin < fallback_begin):
            fallback_begin = begin
        if not fallback_end or (end and end > fallback_end):
            fallback_end = end

    place_begins = [s[0] for s in place_sessions if s[0]]
    place_ends = [s[1] for s in place_sessions if s[1]]
    # A dated fallback is "covered" only when the place sessions carry dates
    # that span it. Guard min()/max() against a place that has a venue but no
    # date (else min() over an empty sequence raises ValueError).
    covered = bool(
        fallback_begin and fallback_end and place_begins and place_ends
        and fallback_begin >= min(place_begins)
        and fallback_end <= max(place_ends))

    if (fallback_begin or fallback_end) and not covered:
        sessions.append("(%s)" % _format_recording_date(
            fallback_begin, fallback_end))

    # Aggregate span across all sessions (place + any fallback).
    all_begins = list(place_begins)
    all_ends = list(place_ends)
    if not covered:
        if fallback_begin:
            all_begins.append(fallback_begin)
        if fallback_end:
            all_ends.append(fallback_end)
    recordingdate = ''
    if all_begins and all_ends:
        recordingdate = _format_recording_date(
            min(all_begins), max(all_ends))
    elif all_begins:
        recordingdate = min(all_begins)
    elif all_ends:
        recordingdate = max(all_ends)

    return {
        'recordingsessions': sessions,
        'recordingplace': venues,
        'recordingcity': cities,
        'recordingdate': recordingdate,
    }
