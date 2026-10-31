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
identical.

**That measurement took only the first work of each recording.** A recording
can perform two works, and then the legacy fuses them into one level: the
Planets album's track 7 performs both suites' "Neptune", and the legacy's
level 0 is the pair `(254f397b, 4be2ad86)`. The new model has no
representation for that - `node_of` maps each work to a node of its own, so
a track with two works has no single chain. Five fixtures have such
recordings (daphnis 14, firebird 1, nutcracker 1, planets 1, requiem 1), and
the measurement above took the first of each pair.

So the 135/180 figure is an upper bound on the agreement, and the gap is
larger than the two classes below. Modelling a track's work as a *set* of
works is the next thing the model needs.

### Two different fusions
There are two distinct things that put several works in one node, and they
must not be confused:

* **A work with two parents** (Tristan's "Prelude and Liebestod"). The
  recording performs one work; that work has two parents, so the node one
  level up holds both of them. This is the fusion the model is built
  around: it is what keeps a movement reached by two routes in one place
  in the tree.
* **A recording that performs two works** (Planets' track 7, which
  performs both suites' "Neptune"). Here the two works are peers.

The legacy fuses the second case as well, and does it by making the two
works a node of their own - the legacy's tree has the key
`('254f397b','4be2ad86')` holding both Neptune works, under the fused key
`('5a75e61c','439c1605')`.

Keeping the two works in separate nodes instead would leave the recording
with two chains rather than one, so a level tag would have to pick one of
them. That is what the model does now, and it is why Planets' track 7
lands in the "Planets Suite extension" group instead of "The Planets,
op. 32".

The two works of a recording need not be peers. The Firebird album's
track 5 carries a performance relation to a movement of the 1919 suite
*and* a second one to the full ballet, and the two are in different
branches: the movement sits under "L'Oiseau de feu, suite de 1919", while
the ballet is a top level work of its own (the suite is only "based on"
it, not a part of it).

That second relation carries the attribute **partial** - it is a
*partial recording of* the ballet, not a full performance of it. The
legacy treats that with the `cwp_partial` option: it invents a
**recording id stand-in** for the work slot and writes the recording's own
id into `~cwp_workid_0`. The Firebird album's track 5 shows it:
`~cwp_workid_0` is `('a06c71e7-...', '036a332c-...')`, and `036a332c` is
the *recording* id, not a work id at all.

### What a recording with several works means
Every recording with more than one work relation was enumerated over the
fixtures in the repository. There are eighteen of them, and they fall into
two groups:

* **The same work twice** (daphnis, all ten of them). MusicBrainz allows
  two relations between one recording and one work, and this release uses
  that: a 'partial' performance relation and a plain one, both pointing at
  the same work. The legacy's `cwp_partial` handling replaces the work id
  of the partial relation with the *recording* id, so the two relations
  then name the same work twice and de-duplicate to one - the recording
  id. The work itself is promoted to level 1.
* **Two distinct works** (daphnis 4, firebird 1, nutcracker 1, planets 1,
  requiem 1). The legacy fuses them into one level 0 node whose id is the
  tuple of the works - Nutcracker's track 1 gives
  `('91421046-...', 'fce0c96b-...')` - and the node's parent is the union
  of the members' parents, reduced by the same rules as any other node.

So one rule covers both groups: **the recording's works form one node**,
whose members are the works (with a 'partial' relation contributing the
recording id instead of the work id), and whose parent is the union of the
members' parents.

The `partial` attribute and the `live` attribute are separate: Requiem's
track 1 carries two *distinct* works for the same music, one of them
marked 'live', and both are kept as works.

The rule should not assume a pair: a recording may perform more than two
works, and the union of their parents may be several works too.

**The union is kept in the tree, but the tag names one of them.** The
legacy's tree key for Requiem's track 1 is the tuple
`('d0be6882-...', '5ba9868e-...')` - both candidates for the level above
the movement - yet `~cwp_workid_1` holds only `d0be6882`.

The legacy has three separate passes that narrow a fused top, and they
run in this order in `process_album`:

1. `_collapse_fused_top_ids` - keep only the parent ids **common to every
   track** under that top. "The version of the work this release actually
   is": if some track under the top belongs to only a subset of the
   parents, that subset is the release's genuine top work.
2. `_reduce_fused_top_to_highest` - drop a constituent that is a
   **descendant** of another constituent. A top work is by definition the
   highest level work, so "Akt III" fused with the opera it is part of
   becomes just the opera.
3. The **vote by name** that `_select_top_work_names` applies to the
   *names* of the remaining candidates, so the group name is single valued.

The new model does the equivalent of (2) - its reduction drops an ancestor
in favour of the more specific descendant - but it has no (1) and no (3).
That is why a track's chain can end in either of two tops, and why the
group name can differ between tracks of one album.

**What the new model does with the same recordings.** The union of the
members' parents is the recording's *candidate* level, and it is reduced
like any other node's parent set. Measured:

| album | the members' parents | the legacy's level 1 |
|---|---|---|
| requiem t1 | `d0be6882`, `5ba9868e` - two editions | `d0be6882` |
| planets t7 | `439c1605`, `5a75e61c` - op. 32 and the extension | `439c1605` |
| nutcracker d1t1 | `f3281e81`, `fd337fdc` - the ballet and the suite | `f3281e81` |
| firebird t5 | `1a00a184` - one | `1a00a184` |

In every case the two candidates are top level works with no parent of
their own, so none of the reduction rules can separate them - they are
siblings, not an ancestor and a descendant. The legacy separates them by
(1) and (3); the new model has no way to, and keeps both. That is the
whole of the remaining gap for these recordings.

- **Two editions of one work** (Swan Lake's op. 20 and the 1895 R. Drigo
  edition, 35 tracks). The legacy picks one of the two as the
  representative; the new model keeps both in one level. The legacy's pick
  comes from its work cache, and the plugin's own
  `CrossAlbumWorkCacheRegressionTestCase` documents that the choice
  depends on which album was processed first - it asserts order
  independence but deliberately does not pin a specific work id. The new
  model's answer is deterministic and keeps both editions.

  The two Swan Lake albums show how the legacy's pick varies:

  | album | legacy top | new model |
  |---|---|---|
  | complete ballet (43 tracks) | `13867eb1` (Drigo edition) | 29 tracks at `{11f48c5e, 13867eb1}`, 8 at `13867eb1`, 6 at `11f48c5e` |
  | Jarvi (19 tracks) | `11f48c5e` (original op. 20) | 13 tracks at `{11f48c5e, 13867eb1}`, 6 at `11f48c5e` |

  So the legacy names one edition for the whole album, and which one
  differs between the two albums. The new model keeps both editions in one
  level for most tracks. Where it names a single edition, that is not
  always because only one route reaches it: the five tracks d1t17 and
  d1t19 to d1t22 reach both editions and still resolve to one. For them
  the container-level reduction drops the unembedded Drigo root
  (`13867eb1`, a top level work) in favour of the embedded "Swan Lake,
  op. 20: Act II" (`f9bdafff`, which sits under the original `11f48c5e`),
  so the chain continues through one edition only.

  **The group is a name, not an id.** `~cwp_work_group` is the shuffle key
  and is matched by exact string equality, so what matters is the *name*
  of the top, not which work id it holds. The two Swan Lake albums make
  that clear: the legacy gives them different `~cwp_workid_top`
  (`13867eb1` for the complete ballet, `11f48c5e` for the Jarvi release)
  but the same `~cwp_work_group`, "Swan Lake, op. 20", for every track of
  both albums. The two editions carry the same name, so they group
  together even though they are different works.

  The legacy's name selection de-duplicates by name: `_select_top_work_names`
  keeps a name only once, so a fused top holding both editions collapses
  to the single name "Swan Lake, op. 20" before the tag is written. On the
  new model's chains the same de-duplication gives one group per album:
  the 29 tracks whose top node holds both editions and the 14 whose top
  node holds one all have the name "Swan Lake, op. 20" (the fused node
  contributes that name twice, which de-duplication collapses).

  So the new model does not need a top-merging step for the group: the
  name already unifies the album. What it does need is the same
  de-duplication when it writes the name, so the fused top contributes
  one name rather than the pair.

  `~cwp_work_top` is allowed to have several values, so the top ids may
  differ between tracks of one album without splitting the group.
- **An intermediate level the legacy drops** (Gayne's 6 tracks). The six
  album movements of "Act I, Scene I (Spring)" - "Carpet-making",
  "Lezghinka", "Dance of Nuneh and Girls", "Gayaneh's Dance", "Awaiting
  the Hunters" and "Duet of Gayaneh and Armen", tracks d1t6 to d1t11 -
  are each listed under *both* the scene and the act, so the two are the
  movement's two `parts` parents. (The scene has a seventh `parts` child,
  which is not on this release and not in the fixture.)
  The scene is itself a part of the act, so the act is an ancestor of the
  scene and the first reduction rule applies: the ancestor is dropped and
  the more specific scene is kept. That gives the new model a fourth
  level, "Act I, Scene I (Spring)", where the legacy has only "Act I".

  The legacy keeps "Act I" and drops the scene level, because it never
  resolved the scene's own chain and so could not see that the scene sits
  under the act. The new model has the whole graph and keeps the more
  specific level.

  **Decision: keep the scene level.** The scene is where the movements
  really live, and the legacy's flatter answer is a side effect of its
  lookup order rather than a considered choice. Pinned by
  `test_the_scene_level_is_kept`.

The Swan Lake difference is not pinned by a test: it is a decision about
what the tags should say, not a defect. Reproducing the legacy exactly
would mean reproducing its lookup order, which is what this model exists to
remove.

### The tree is a tree, not a chain
A track's level chain (`work_0 .. work_N`) is the path from the track's work
up to the top. The tree around it is wider than that path: two tracks under
one top have separate branches that meet only above them.

The Tristan album shows the difference. Top down from the opera:

```
Tristan und Isolde, WWV 90
  Akt I
    Akt I, Vorspiel
  Akt III
    Akt III, Szene III
      {Akt III Szene III excerpt, Akt I, Vorspiel}   <- the fused node
        Prelude and Liebestod
      {Akt III Szene III excerpt}
```

The node at depth 3 holds an Akt III branch work and an Akt I branch work in
one group. That is the fused container, and it is deliberate: it is what
keeps "Prelude and Liebestod" in one place in the tree even though it is
reached by two routes. Without it the movement would appear twice.

The container's own parent is the single work `{Akt III, Szene III}`. The
other member of the container, "Akt I, Vorspiel", leads to "Akt I", which
reaches the opera in one step while "Akt III, Szene III" reaches it in two,
so the flatter branch rule drops "Akt I" from the container's parent set.

That is why the chain from the movement is

```
Prelude and Liebestod
  {Akt III Szene III excerpt, Akt I, Vorspiel}
  Akt III, Szene III
  Akt III
  Tristan und Isolde, WWV 90
```

which is the same five level chain the legacy produces for this track. The
legacy reaches it by merging the two routes into one tuple and reducing
that tuple; the new model keeps the routes apart and reduces the
container's parent set instead.

The two branches are siblings at the top of the tree, and the fused node
holds the material of both - but the fused node has exactly one parent, so
the levels above it are single works. Every node in the hierarchy has at
most one parent, in every fixture: that is what makes the chain to a track
unambiguous. (Test:
`test_every_node_has_at_most_one_parent`.)

The "Akt I" branch is not lost from the tree: "Akt I" is still a node under
the opera, and "Akt I, Vorspiel" is still a node of its own. Only the
*chain* to the movement goes through the Akt III branch.

Note that the movement's two links to "Akt I, Vorspiel" and to the Akt III
Liebestod excerpt are both `arrangement` relations, not `parts`:
MusicBrainz records "Prelude and Liebestod" as an arrangement of both of
them. The movement has no `parts` relation at all, so the parent resolution
falls through to `arrangement` and finds two parents instead of one. That
is why one level holds two works - one work with two
`arrangement`-relation parents, rather than one parent. The `parts` links
are what give each of those two works a place in the hierarchy, and those
are separate: "Akt I, Vorspiel" is under "Akt I", the Akt III excerpt under
"Akt III, Szene III".

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
   The converse check is what keeps the rule symmetric: when both parents
   reach a shared work at the same depth, neither is the flatter one, so
   neither is dropped. The converse is checked with "any", not "all": it
   only asks whether the other parent also reaches back, which is enough
   to tell the two parents apart.
   (Regression tests:
   `test_a_parent_that_carries_its_own_branch_is_kept` and
   `test_parents_reaching_a_shared_work_at_the_same_depth_are_kept`.)

The reduction must also be applied to the **container's** parent set, not
only to a work's own parents. Without that the Tristan chain visited the
opera twice: "Prelude and Liebestod" is reached through the Akt I Vorspiel
and through the Akt III Liebestod, and the two routes run at different
depths. Regression tests:
`test_a_work_appears_at_most_once_in_a_chain` (against the real fixture) and
`test_the_flatter_branch_is_dropped` (against a synthetic shape, since the
fixture alone does not isolate rule 3).
