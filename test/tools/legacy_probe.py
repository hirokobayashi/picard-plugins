"""Run the real legacy plugin against a fixture directory.

This is the way to check the adapter against the legacy on a case that no
fixture covers: it drives the real ``PartLevels.add_work_info`` over a
directory of MusicBrainz JSON, exactly as the legacy's own integration test
does, and writes the tags it produces.

Two things have to be right for the result to mean anything:

* the track's fifth field - the work id - must name a work that has a
  ``work_<id>.json`` in the directory, or the legacy's first work lookup
  fails and the whole album is dropped;
* the harness must reproduce a recorded fixture before its output is
  believed. The daphnis album is the check: the tags it gives must be the
  ones in ``test/fixtures/legacy_tags.json``.

The legacy's own integration test for the tristan album is the reference
for the shape of the flow; this module reuses that test class for its
setup and its runner, so there is one flow, not two.
"""
import inspect
import json
import os
import sys
import textwrap
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import test.test_classical_extras as T

_SRC = textwrap.dedent(inspect.getsource(
    T.TristanFullAlbumIntegrationTestCase._run_full_album))


def run(fixdir, rel, tracks, partial=True, drain="fifo"):
    """Run the legacy over the fixture directory and return its tracks.

    ``drain`` is the order the work lookups are answered in: "fifo",
    "lifo", or a seed for a random order. The legacy's tags can depend
    on it - see the note in AGENTS.md about the shapes where they do.
    """
    src = _SRC
    if partial:
        assert '"cwp_partial": False,' in src
        src = src.replace('"cwp_partial": False,', '"cwp_partial": True,')
    ns = {'_ALL_OPTION_DEFAULTS': T._ALL_OPTION_DEFAULTS,
          'random': __import__('random')}
    exec(compile(src, 'runner', 'exec'), ns)
    runner = ns['_run_full_album']

    class Synth(T.TristanFullAlbumIntegrationTestCase):
        _FIXDIR = fixdir
        _REL = rel
        _TRACKS = tracks
        _run_full_album = runner

    case = Synth('test_result_is_independent_of_lookup_order')
    case.setUp()
    try:
        # The drain order is the order the work lookups are answered in,
        # which is the order the legacy's tags can depend on.
        return runner(case, drain=drain)
    finally:
        # The runner registers cleanups that restore the aliases and the
        # log function it stubbed out. They only run when the cleanups
        # are done, so they are done here - or the stubs stay for the
        # rest of the process.
        #
        # The plugin registers script functions and leaves picard.config
        # as a Mock, and both leak into the tests that run after this
        # one. The legacy's own test class cleans up the same way.
        #
        # A cleanup that raised would skip the second step, so the two
        # are nested.
        try:
            case.doCleanups()
        finally:
            T.ClassicalExtrasTestCase._cleanup_global_state()


def work(wid, title, parent=None):
    d = {"id": wid, "title": title, "type": None, "type-id": None,
         "attributes": [], "disambiguation": "", "language": "zxx",
         "iswcs": [], "languages": ["zxx"], "relations": []}
    if parent:
        d["relations"] = [{"type": "parts", "direction": "backward",
                           "target-type": "work", "target-credit": "",
                           "source-credit": "", "attributes": [],
                           "attribute-ids": {}, "attribute-values": {},
                           "ordering-key": 1,
                           "work": {"id": parent, "title": parent, "type": None,
                                    "type-id": None, "attributes": [],
                                    "disambiguation": "", "language": "zxx",
                                    "iswcs": [], "languages": ["zxx"]}}]
    return d


def rel(wid, partial=False, key=1):
    return {"type": "performance", "direction": "forward",
            "target-type": "work", "target-credit": "", "source-credit": "",
            "attributes": ["partial"] if partial else [],
            "attribute-ids": {}, "attribute-values": {}, "ordering-key": key,
            "work": {"id": wid, "title": wid, "type": None, "type-id": None,
                     "attributes": [], "disambiguation": "", "language": "zxx",
                     "iswcs": [], "languages": ["zxx"]}}


def tracks_of(directory, label="t1"):
    """The tracks for a fixture directory with one recording.

    The work id seeds the legacy's first work lookup, so it has to name
    a work that has a ``work_<id>.json`` in the directory.
    """
    with open(os.path.join(directory, 'rec_%s.json' % label)) as f:
        recording = json.load(f)
    works = [r['work']['id'] for r in recording.get('relations') or ()
             if r.get('type') == 'performance']
    return [(label, 1, 1, recording['id'], '; '.join(works))]


def readings_of(directory):
    """The works and the recordings of a fixture directory."""
    works = []
    for name in sorted(os.listdir(directory)):
        if not name.startswith('work_'):
            continue
        with open(os.path.join(directory, name)) as f:
            works.append(json.load(f))
    recordings = []
    for name in sorted(os.listdir(directory)):
        if not name.startswith('rec_'):
            continue
        with open(os.path.join(directory, name)) as f:
            recordings.append(json.load(f))
    return works, recordings


def write_case(d, works, recordings, label="t1", seed=None):
    """Write a fixture and return the tracks for the harness."""
    os.makedirs(d, exist_ok=True)
    for w in works:
        with open(os.path.join(d, 'work_%s.json' % w['id']), 'w') as f:
            json.dump(w, f)
    for rid, rels in recordings:
        with open(os.path.join(d, 'rec_%s.json' % label), 'w') as f:
            json.dump({"id": rid, "relations": rels}, f)
    return [(label, 1, 1, list(recordings)[0], seed or works[0]['id'])]
