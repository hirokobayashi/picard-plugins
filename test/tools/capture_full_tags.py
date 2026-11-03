# -*- coding: utf-8 -*-
"""Capture the legacy's full tag set for the recorded releases.

The original oracle, ``test/fixtures/legacy_tags.json``, records only the
ten hierarchy keys (the ``~cwp_workid_*`` / ``~cwp_work_top`` /
``~cwp_work_group`` / ``~cwp_part_levels`` / ``~cwp_work_part_levels``
tags) - the ones the adapter wrote when the parity harness was built.
This script re-runs the real legacy plugin over the same nine fixture
directories and records **every** ``~cwp_`` tag it writes, so that the
name tags (``~cwp_work_N``, ``~cwp_part_N``) and the genre/date tags have
an oracle too.

Faithfulness check: before writing anything, the ten keys the old oracle
holds are compared with what this run of the legacy produces. The old
oracle was captured once from the real legacy, so a run that disagrees
with it is a run that cannot be trusted - the script says so per album
and writes nothing for it.

Usage::

    QT_QPA_PLATFORM=offscreen .venv/bin/python test/tools/capture_full_tags.py

Writes ``test/fixtures/legacy_tags_full.json`` for the albums that pass
the check.
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

_FIXTURES = os.path.join(_ROOT, 'test', 'fixtures')

# The albums and their "partial" option, as the parity test sets them.
USE_PARTIAL = {
    "daphnis": True,
    "firebird": True,
    "gayne": False,
    "nutcracker": True,
    "planets": False,
    "requiem": True,
    "swanlake_full": True,
    "swanlake_jarvi": True,
    "tristan": False,
}

#: The keys the old oracle records - the ones that must match exactly.
OLD_KEYS = (
    '~cwp_part_levels',
    '~cwp_work_group',
    '~cwp_work_part_levels',
    '~cwp_work_top',
    '~cwp_workid_0',
    '~cwp_workid_1',
    '~cwp_workid_2',
    '~cwp_workid_3',
    '~cwp_workid_4',
    '~cwp_workid_top',
)


def _load(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def _as_picard_stores(value):
    """A tag value as Picard stores it, so tuples and lists agree."""
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        if not value:
            return None
        if len(value) == 1:
            return value[0]
    return value


def _ids(value):
    """A workid tag's ids, whether joined or held as a list.

    The old oracle records a multi-id level the way Picard stores it - a
    bare string for one id, ``"; "``-joined for more - and this run's
    metadata may hold either form. Both come back as a list of ids here,
    which is how the parity test compares them too.
    """
    value = _as_picard_stores(value)
    if value is None:
        return None
    if isinstance(value, str):
        return [part.strip() for part in value.split(';')]
    return list(value)


def _tracks_of(fixdir, labels):
    """The track tuples the probe needs, one per recording fixture."""
    tracks = []
    for label in labels:
        rec = _load(os.path.join(fixdir, 'rec_%s.json' % label))
        works = [r['work']['id'] for r in rec.get('relations') or ()
                 if r.get('type') == 'performance']
        digits = ''.join(ch for ch in label if ch.isdigit())
        tracks.append((label, 1, int(digits), rec['id'], '; '.join(works)))
    return tracks


def main():
    sys.path.insert(0, os.path.join(_ROOT, 'test', 'tools'))
    import legacy_probe

    old_oracle = _load(os.path.join(_FIXTURES, 'legacy_tags.json'))
    full = {}
    for album in sorted(USE_PARTIAL):
        fixdir = os.path.join(_FIXTURES, album)
        labels = sorted(
            name[4:-5] for name in os.listdir(fixdir)
            if name.startswith('rec_') and name.endswith('.json'))
        recorded = old_oracle[album]['tracks']
        tracks = _tracks_of(fixdir, labels)
        result = legacy_probe.run(
            fixdir, album, tracks, partial=USE_PARTIAL[album])
        captured = {}
        mismatches = []
        for label in labels:
            metadata = result[label].metadata
            tags = {tag: metadata[tag]
                    for tag in sorted(metadata)
                    if tag.startswith('~cwp_')}
            captured[label] = tags
            # The faithfulness check: the keys the old oracle holds must
            # read the same as this run's. The workid keys are compared
            # as ids - the recorded oracle holds them "; "-joined, which
            # is Picard's storage of a tuple, not a different value.
            for key in OLD_KEYS:
                want = recorded.get(label, {}).get(key)
                if key.startswith('~cwp_workid'):
                    same = _ids(want) == _ids(tags.get(key))
                else:
                    same = _as_picard_stores(want) == _as_picard_stores(
                        tags.get(key))
                if not same:
                    mismatches.append((label, key, want, tags.get(key)))
        if mismatches:
            print('%s: %d mismatches against the recorded oracle - '
                  'not written' % (album, len(mismatches)))
            for label, key, want, got in mismatches[:4]:
                print('   %s %s: recorded %r, this run %r'
                      % (label, key, want, got))
            continue
        full[album] = {
            'partial': USE_PARTIAL[album],
            'tracks': captured,
        }
        print('%s: %d tracks, the recorded keys all match' %
              (album, len(labels)))

    out = os.path.join(_FIXTURES, 'legacy_tags_full.json')
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(full, handle, indent=1, sort_keys=True,
                  ensure_ascii=False)
    print('written: %s (%d albums)' % (out, len(full)))


if __name__ == '__main__':
    main()
