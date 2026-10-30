# AGENTS.md — picard-plugins repo notes

## Classical Extras plugin (`plugins/classical_extras/`)

### Test harness
- Plugin tests live in `test/test_classical_extras.py`, using `test.plugin_test_case.PluginTestCase`.
- The plugin reads `config.setting` at import/install time, so seed it with
  every default from `const.py` before installing the plugin (see
  `_ALL_OPTION_DEFAULTS` in the test file). Without this, `KeyError`/`log_info`
  failures occur at install.
- `unload_plugin` does not fully clear `sys.modules`, so the second+ test in a
  class fails with `NameError: name 'const'`. Fix: load the plugin once per test
  class via a `_mod` class-level cache (`ClassicalExtrasTestCase._mod`).
- Run tests with the offscreen Qt platform:
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest test.test_classical_extras`

### Top-work logic (`PartLevels.process_album`)
- The top-work decision logic was extracted into `_resolve_chosen_tops(self,
  release_id, album, track_tops)` and `_is_collection_top(self, top)` so it is
  unit-testable in isolation. `process_album` calls the helper and sets
  `self.chosen_top`.
- Chosen top for a track shared between several top works:
  1. single non-collection candidate (per README "highest level work ... which
     is not a collection"); else
  2. most-selected top (highest unambiguous track tally, ties broken by order
     in `self.top[album]`); else
  3. first candidate in discovery order.
- With zero unambiguous tracks, `most_selected` falls back to the first top in
  `self.top[album]` (because `0 > -1`), not `None`. This is harmless: an empty
  `track_tops` yields an empty `chosen_top`, and downstream pruning removes
  unused tops.

### Tasks implemented (v2.0.14)
- Task A: `cyrillic_to_latin(name, sort_name)` always removes the
  patronymic/middle name (via `remove_middle(unsort(sort_name))`) and always
  returns Latin script (transliterates via `get_roman` when the sort-name is
  non-Latin). `get_roman` guards `index+1` out of range for a trailing
  uppercase cyrillic letter.
- Task D: `cwp_excluded_works` option (const.py WORKPARTS_OPTIONS, default
  `''`) excludes works by MusicBrainz work id. Exclusion is applied by
  index-descending deletion so `new_workIds`/`new_works` stay in sync; it is
  `KeyError`-safe (falls back to `''`) for option blobs saved by older plugin
  versions. UI added to `options_classical_extras.ui` +
  `ui_options_classical_extras.py`.

### Recording place/date tags (pure core only)
- `recording_session_tags(relations)` is a module-level pure function (near
  line 2990, after `blank_if_none`). It takes a recording's already-parsed
  `relations` list (as from `/recording?inc=place-rels+artist-rels`) and
  returns a dict: `recordingsessions`, `recordingplace`, `recordingcity`,
  `recordingdate`. It does NO web lookups and writes NO tags — the caller wires
  the fetch and hands the relations in. Do NOT touch webservice/RecordingQueue
  for this feature.
- Place/date are PAIRED per `target-type:place` + `type:"recorded at"` rel; the
  begin/end live on each place relation. Sessions are sorted by begin date and
  formatted `"<Venue>, <City> (<date>)"`. `recordingplace`/`recordingcity` are
  deduped canonical names (first-seen order).
- Date fallback: dated artist rels (`conductor`/`orchestra`/`ensemble`/
  `performer`) give a date-only `"(<date>)"` session ONLY when its span is NOT
  covered by a place session (see `_RECORDING_DATE_FALLBACK_TYPES`,
  `covered` logic). Case (b) proves the conductor span 2018-04-19..04-22 is
  covered by the two place sessions -> no third session.
- `_format_recording_date(begin, end)`: ISO 8601, single value when begin==end
  (precision-match; month precision -> `"2024-09"` not a range), else
  `"<begin> - <end>"` with SPACED ASCII hyphen (`_RECORDING_DATE_RANGE_SEP`).
- `recordingcountry` is deferred (TODO in the function); needs area-hierarchy
  lookups. `target-credit` (credited-as venue in the display tag) is also a
  TODO; v1 uses canonical `place.name` everywhere because no fixture has a
  populated target-credit.
- Tests: `RecordingSessionTagsTestCase` in test_classical_extras.py loads
  real MB JSON fixtures from `test/fixtures/` (captured once). 3 real-recording
  tests + 2 synthetic edge-case tests (precision-match month, duplicate-venue
  dedup). Each was proved RED first (function absent -> AttributeError; edge
  tests proved via targeted mutation showing failure, then reverted).

### `process_trackback` crashes / regressions (Alina album investigation)
- The `depth != 0` branch of `process_trackback` previously did
  `tracks = child_response[1]` unconditionally. When a top work has children
  defined in MusicBrainz (so `depth != 0`) but none of the child subtrees carry
  tracks for this album (tracks attached directly to the top), the helper
  `process_trackback_children` returns `None`, and the unguarded subscript
  raised `TypeError: 'NoneType' object is not subscriptable`. Because
  `process_album` calls `process_trackback` with no try/except, the exception
  aborted the whole album loop: the top's own tracks were dropped AND every
  subsequent top's tracks were skipped too. This matched the reported "Alina"
  (album `3e73f4f5`) tracks-dropped symptom.
  Fix: guard `if child_response is not None: tracks = child_response[1]` and
  fall through to the depth-0 loop so the top's own tracks are still processed.
  Regression test: `test_process_trackback_root_has_children_no_child_tracks`.
- Earlier parallel-lists regression (also tracked under Alina): when a top
  work has multiple tracks sharing it, `process_trackback`'s track/title/work/
  tracknumber lists must stay the same length (caller zips track with
  tracknumber). Tests: `test_process_trackback_depth0_keeps_all_tracks_parallel`,
  `test_process_trackback_children_then_root_stays_parallel`,
  `test_process_trackback_fur_alina_tracks_kept`.

## New work-hierarchy model (`plugins/classical_extras/hierarchy/`)
Picard-independent replacement for the legacy hierarchy model, in preparation
for a Picard 3 port. No `import picard` anywhere in the package - enforced by a
test that both scans the sources and blocks the import.

### Legacy semantics that had to be reproduced (do not "simplify" these away)
- **Parent resolution is an ordered fallback**, not just `parts` relations:
  `parts` (backward) -> `arrangement` (backward) -> `medley`. The first type
  that yields a parent wins. This matters for the Tristan album, where
  "Prelude and Liebestod" reaches the opera only via an `arrangement`
  relation, so treating arrangements as non-hierarchical leaves it with no
  top work. A `part of collection` relation names a grouping work, not a
  genuine parent, so it is skipped when collections are disabled.
- **Redundant parent reduction**: drop a parent that is an ancestor of another
  parent, and drop a top-level parent when another parent is embedded in a
  larger work. Without it a work sits one level flatter than its siblings.
  Verified against the Liszt fixture (drops the broad "Annees de pelerinage"
  in favour of the specific "Premiere annee: Suisse").
- **Legacy node keys are the work's OWN id tuple**, with `['parent']` holding
  the parent ids. `partof[PARENT_KEY] = [CHILD_KEY]`.

### Node model
- Each work is a node of its own: `NodeId(frozenset({work}))`.
- The node CONTAINING a work is `NodeId(frozenset(parents(work)))`. A work
  with two parents is therefore contained by a fused node holding both.
- **A fused container must itself be linked upward**, to the node holding the
  parents of the works it holds. Omitting this makes the container an orphan
  root: the movement below it is invisible from the top, `path_to_top` stops
  short and does not end in `tops`. That is the "track lost its top work"
  defect resurfacing. Regression test:
  `test_work_reachable_by_two_routes_still_reaches_the_top`.
- `NodeId` uses a `frozenset` so `('a','b')` and `('b','a')` are the same
  node. The legacy tuple keys made them different, which is why the same
  movement could be filed twice under two key spellings.

### Cycle breaking
Uses the DFS back-edge rule: drop the edges pointing back to a work already
on the current search path. Validated as acyclic AND maximal on 5000 random
graphs (`_PROPERTY_SEEDS` in the test file; no dropped edge could have been
kept).
- The obvious per-edge alternative ("drop this parent if the child is an
  ancestor of the parent") is WRONG: inside a cycle every edge satisfies it,
  so the whole cycle is removed and the work is disconnected from its album.
- The traversal is iterative (a deep hierarchy must not exhaust the stack)
  and visits works in sorted id order, so the result does not depend on the
  order the MusicBrainz lookups return in.

### Verified against the legacy
On the Tristan fixture the new model puts every track under the same top
work as the legacy pipeline, and produces the legacy's tree without the
legacy's duplicate entries. That per track mapping is pinned by
`test_tristan_track_tops_match_the_legacy`, which records the legacy's
`chosen_top` mapping track by track. Pinning the mapping per track, rather
than just the set of top works, is what makes the check bite: re-heading a
single track under a different top work is caught even though every top
work still appears on the album. The tree comparison itself was checked by
hand and is not pinned by a test.

### Level chains (work_0 .. work_N)
The legacy's per track chain of `~cwp_work_N` levels was measured against the
new model's chain for 180 tracks across 7 fixtures. 135 of them (75%) are
identical. The differences are two known classes:

- **Two editions of one work** (Swan Lake's op. 20 and the 1895 R. Drigo
  edition, 35 tracks). The legacy picks one of the two as the
  representative; the new model keeps both in one level. The legacy's pick
  comes from its work cache, and the plugin's own
  `CrossAlbumWorkCacheRegressionTestCase` documents that the choice
  depends on which album was processed first - it asserts order
  independence but deliberately does not pin a specific work id. The new
  model's answer is deterministic and keeps both editions.
- **An intermediate level the legacy drops** (Gayne's 6 tracks). The
  movement "Carpet-making" is listed under both "Act I" and "Act I, Scene I
  (Spring)". The legacy keeps "Act I" and drops the scene level; the new
  model keeps the scene. The legacy drops it because it never resolved the
  scene's own chain, so it could not see that the scene sits under the act.
  The new model has the whole graph and keeps the more specific level.

Neither difference is pinned by a test: they are decisions about what the
tags should say, not defects. Reproducing the legacy exactly would mean
reproducing its lookup order, which is what this model exists to remove.

### Reduction of a redundant parent
`reduce_parents` applies three rules, in this order:

1. A parent that is an **ancestor** of another parent is dropped (the more
   specific one is kept). This is the Liszt case.
2. A **top level** parent is dropped when another parent is embedded.
3. The **flatter branch** rule: a parent that reaches the other parent's
   ancestors in a single step is dropped, when the converse does not hold.
   Every one of the flatter parent's own parents must be reachable that
   way - a parent that also leads somewhere the other one cannot reach
   carries a branch of its own, and dropping it would lose that branch.
   (Regression test: `test_a_parent_that_carries_its_own_branch_is_kept`.)

The reduction must also be applied to the **container's** parent set, not
only to a work's own parents. Without that the Tristan chain visited the
opera twice: "Prelude and Liebestod" is reached through the Akt I Vorspiel
and through the Akt III Liebestod, and the two routes run at different
depths. Regression tests:
`test_a_work_appears_at_most_once_in_a_chain` (against the real fixture) and
`test_the_flatter_branch_is_dropped` (against a synthetic shape, since the
fixture alone does not isolate rule 3).
