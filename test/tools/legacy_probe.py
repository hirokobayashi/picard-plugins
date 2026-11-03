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
        return runner(case)
    finally:
        # The plugin registers script functions and leaves picard.config as
        # a Mock, and both leak into the tests that run after this one.
        # The legacy's own test class cleans up the same way.
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
