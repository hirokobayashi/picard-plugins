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
- `recording_session_tags(relations, name_overrides=None)` is a module-level
  pure function (near line 3116, after `blank_if_none`). It takes a
  recording's already-parsed
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

### The fused-top collapse (PR #11) and the two shapes it does not fix
PR #11 fixes the case where a recording is linked to both a child work
and its top-level parent: `_reindex_top` collapsed the hierarchy to the
top work and dropped the child. Two *neighbouring* shapes have the same
symptom and are **not** fixed by it - they behave the same before and
after:

- **a skipped intermediate level.** A part of B, B part of C, C top,
  and the recording references A and C. The circular-reference guard in
  `work_process` - the block that drops a parent which is a descendant
  of the work, and which writes the `~cwp_error` "5. Found parent which
  which is descendant of child" - refuses the B -> C edge before the
  re-indexing, so the whole B level goes with it: `part_levels` is 0 and
  the fused names leak into `~cwp_work_top`. The keep-existing guard
  cannot fire, because there is no `(C,)` tree that reaches the fused
  node.
- **multiple parents.** A is part of both B and D, both top-level, and
  the recording references A and B. The fused node lands in a different
  top's tree, so the `(B,)` top owns no tree that reaches it - either
  there is no tree under `(B,)` at all, or the reachability check is
  false - and the childless fused tree overwrites.

So PR #11 fixes the shape it describes, not the bug in general.

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
  `test_process_trackback_zip_does_not_truncate`.

## New work-hierarchy model (`plugins/classical_extras/hierarchy/`)
Picard-independent replacement for the legacy hierarchy model, in preparation
for a Picard 3 port. No `import picard` anywhere in the package - enforced by a
test that both scans the sources and blocks the import.

### Where this stands
PR #12 (the model) and PR #13 (the adapter) are merged into `my-mods`. The
**shipped plugin still does not use either** - `plugins/classical_extras/__init__.py`
is untouched and imports nothing from `hierarchy/`. The adapter writes six
tags (`~cwp_workid_*`, `~cwp_workid_top`, `~cwp_work_top`, `~cwp_work_group`,
`~cwp_part_levels`, `~cwp_work_part_levels`); the plugin writes about thirty
more (the composer, arranger and key tags, the `~cwp_part` / `~cwp_extended_*`
names, the groupheading tags and so on), and those are still the legacy's
code.

So the remaining work, in order:

1. **Swap the plugin's hierarchy over to the adapter.** The legacy's
   `process_album` / `process_trackback` path is the thing to replace. The
   parity is measured - 161 of 218 tracks identical in every tag, and the
   three tags that matter (`~cwp_workid_0`, `~cwp_work_top`, `~cwp_work_group`)
   identical on all 218 - so the swap is measurable as it is made.
2. **Move the other tag features off Picard.** They are the bulk of what is
   left, and each one is Picard-coupled in its own way (`config.setting`, the
   web service, `TrackMetadata`).
3. **The Picard 3 plugin API itself.** The plugin declares
   `PLUGIN_API_VERSIONS = ["2.0" ... "2.7"]`; Picard 3 needs the new API.
4. **Then the legacy file can go.** `plugins/classical_extras/__init__.py` is
   10,899 lines, and it carries the pre-existing lint errors - they go with
   it.

### The full-tag oracle (`test/fixtures/legacy_tags_full.json`)
The original oracle records only the ten hierarchy keys. The full oracle,
made by `test/tools/capture_full_tags.py`, records **every** `~cwp_` tag
the legacy writes for the same 218 tracks - 26 tag families including the
name tags (`~cwp_work_N`, `~cwp_part_N`) and the genre/date tags.

Before writing an album it re-checks the ten keys the old oracle holds;
all nine albums reproduce them, so the capture is the legacy itself, not
a paraphrase.

**The name tags already match 623 of 674 levels (92%)** just from the
model of `WorkNames` - the `~cwp_work_N` of the legacy is the canonical
MusicBrainz title of the work that the level id names. The 51 differences
split into two kinds, neither of which is a missing response file:

- **14 levels put the id of the recording itself at level 0.** The
  partial-performance representation names the recording where the
  legacy names the work it performs, and `WorkNames` has no name for a
  recording. The work response is in fact present - the `~cwp_work_0`
  of daphnis d1t1 is exactly the title of `work_210c1890`, the work
  the recording performs. Closing this means resolving the recording
  id to the title of its performed work, not fetching anything.
- **37 are the documented divergences of the model.** The Gayane scene
  level the legacy drops, both Swan Lake editions where the legacy
  picked one, extra works held in a level, and the Tristan ordering.
  A position-wise comparison turns each structural difference into
  adjacent mismatches - differences the test suite already documents,
  `DIFFERING_TAGS` for 36 of the 37, and the Tristan arrangement order for
  the last (its level ids match the legacy; only the name order differs).

### The probe (`test/tools/legacy_probe.py`)
Runs the *real* legacy plugin over a directory of MusicBrainz JSON, exactly
as the legacy's own integration test does. It is how a case no fixture covers
is checked against the legacy.

**A probe that does not reproduce a recorded album proves nothing**, and the
probes used while the adapter was written did not - the track's work id has to
name a work with a `work_<id>.json` in the directory, or the legacy's first
work lookup fails and the whole album is dropped. `LegacyProbeTestCase`
checks the probe against the recorded daphnis album before its output is
believed.

The legacy's *own* test suite still writes to the user's real
`~/.config/MusicBrainz/Picard/Classical_Extras/session.log`; the probe no
longer does. That is worth boxing later.

Two things to keep in mind when the swap happens:

- **the adapter writes every level, with no depth cap** - the legacy's
  recursion has none either, so a chain of any depth is written the
  same way by both. But the probe test compares levels 0 to 3 only.
  That reaches every level daphnis has - its chains end at 3 - but the
  fixtures' deepest chain is tristan's level 4, and tristan is never
  probed, so its level 4 rests on `LegacyParityTestCase` alone. A wrong
  *depth* for daphnis would still be caught, by `~cwp_part_levels`; a
  spurious level 4 id would not be, by that test.
- **the other tag features lean on the traversal.** The genre, key, date
  and structure tags are written from inside it - `self.write_tags(...)`
  is called from `process_trackback` and `set_metadata` - and the rest
  depend on tags the
  traversal wrote, the artist tags being a separate pass keyed off
  `~cwp_workid_0`. So the legacy's path cannot fully go away until both
  the hierarchy and the other features have moved.

### The adapter (`hierarchy/adapter.py`)
The seam between MusicBrainz's JSON and the model. It reads the work and
recording responses and writes the hierarchy tags. Two things the model
deliberately does not care about have to be restored here, because the
legacy's tags carry them:

- **the order of the ids inside a level.** The legacy keyed a node by a
  *tuple*, and the order in the tuple was the order the relations came back
  in. The model holds a node's works as a *set*, so the order has to be put
  back. The rule, measured over every fixture: the first level is the
  recording's **performance** relations in order, with a partially performed
  work represented by the **recording id** in the relation's place. A work
  related twice is decided by its **first** relation - see the section on
  the first relation below. The levels above the first take their order from
  the relation of the work the level was reached through.
- **the format of a level's ids.** The legacy hands Picard a **tuple** of work
  ids, and Picard stores a tuple of one id as a bare string and a tuple of
  more as a list. So a level of one id reads as `abc...` and a level of two
  reads as a list of two. The adapter hands Picard the same tuple, so the
  stored text is the legacy's.

**One bad track must not take the release with it.** The adapter skips
what it cannot use, rather than raising out of the tagging:

- a response that is not an object, or that has no id, is skipped - on
  both the recording side and the work side;
- a relation that is not an object, or whose work is not an object, is
  skipped;
- attributes that are not a list are no attributes - a string would be
  searched as a list of characters, and `'partial' in 'partially'` is
  true;
- a chain whose tip the hierarchy does not hold leaves that one track
  with no tags;
- a hierarchy with a cycle, or with a child it does not hold, leaves the
  track with no tags too - the depth would be wrong, and a wrong depth
  is worse than none. The whole track is left, group and all, rather
  than half a track: the group is named from the same hierarchy, so it
  would be wrong too.

A count of tracks below one is refused, and a count for a recording
that is not in the release is reported in `unused_multiplicity`.

**The vote for a top's name is per track.** The legacy's vote is built
from the tracks, so a recording that is on two tracks votes twice. The
adapter counts the tracks each recording is on - a release that lists
the same recording twice is that recording on two tracks - and a
multiplicity given for the recording says so instead. The vote picks
among a track's *own* candidate names: a track whose only candidate is
one work keeps that name however the album votes.

**One vote serves both the top's name and the group.** The legacy
works `~cwp_work_top` and `~cwp_work_group` out of the *same* vote and
the same names - the group is the top's name reduced to one value, so
that a tie does not split the group. So the two tags of a track cannot
disagree, and the adapter works them out together.

The group is reduced from the names the top's tag *holds*, not from the
candidate names: two names that differ only in punctuation normalise to
the same thing, so both can win the vote, and the group has to be one
of the names that won. The name kept is the first of them in the order
they came in, which is the legacy's order.

The legacy also sorts a node's *display names* by the works'
`ordering-key`. That is the `~cwp_work_N` tag, not the hierarchy ids
the adapter writes, so it is outside what the adapter does.

The `partial` substitution only applies when the plugin's `cwp_partial`
option is on. With it off, a partially performed work is an ordinary one.

**Only a 'performance' relation names a work the recording performs.**
The legacy matches a relation by its target - any relation whose target
is a work with that id - so it would read a 'parts' relation as a
performance. The adapter asks for the type, because a 'parts' relation
is not a performance and reading it as one would put the recording
under a work it does not perform. No release has such a relation:
MusicBrainz's only work relation is 'performance'.

**A work performed only in part is the parent of the recording id** -
but only when there is no work performed in full. The legacy takes the
parents of the works performed in full, and looks a partly performed
work up as a node of its own; so a recording that also performs a work
in full gets that work's parents, and one that performs only partly
performed works gets the partly performed works.

**Where the legacy is not deterministic, the adapter is.** A recording
that performs two works whose parent trees do not meet makes the legacy
fuse nodes as it walks, so its tags depend on the order the lookups come
back in - the same album gives different tags from run to run. The
adapter keeps every work and the relations' order, so it is the same
every time. No recorded album has that shape, so the fixture parity
does not prove anything about it.

It was measured, not assumed: the `divergent` fixture is a recording
that performs two works under two parents that do not meet, and the
legacy gives `('parent-a', 'parent-b')` for one lookup order and
`('parent-b', 'parent-a')` for another.
`LegacyNonDeterminismTestCase` runs both orders and checks they differ,
so the note stays a fact.

`test/tools/legacy_probe.py` runs the real legacy over a fixture
directory, which is how these cases were checked. It reuses the legacy's
own integration test flow, and `LegacyProbeTestCase` checks that it
reproduces the recorded daphnis album - so its output can be believed.
**A probe that does not reproduce a recorded album proves nothing**, and
an earlier version of one did not: the track's work id has to name a
work that has a `work_<id>.json` in the directory, or the legacy's first
work lookup fails and the whole album is dropped.

A work related twice is in the performance's `partial` **and** its
`complete` set: the recording stands in for the work, and the work itself
still contributes - it is the parent of the recording id one level up.

**Measured against the legacy** (the ids, the level counts, the top work
and the group):

- `~cwp_workid_0`, `~cwp_work_top` and `~cwp_work_group` are identical on
  **all 218** tracks of the nine releases.
- `~cwp_part_levels` and `~cwp_work_part_levels` differ on 11 and 2 tracks.
- 161 of the 218 tracks are identical in every tag.
- The 57 that differ are the model's own decisions, and they are the ones
  the model already documents. Counted by cause, so that a track differing
  in more than one way is counted once:
  - **45** where the top holds more works than the legacy's - 29 of Swan
    Lake's 43 tracks, 13 of the Jarvi release's 19, and one each of
    Nutcracker, Planets and Requiem;
  - **12** that differ elsewhere in the chain - the 6 Gayne scene-level
    tracks, which end at the same work but one level deeper, and the 6
    Swan Lake tracks that end at the original edition rather than the
    Drigo one.

  The test's own lists are `DIFFERING` (44 tracks whose ids differ) and
  `HOLDING_MORE` (13 tracks whose level holds the legacy's ids and more).
  The 44 and the 13 add up to the 57, but they are not the same split as
  the 45 and the 12 above: a track can differ in the ids *and* hold more
  of them, so the two lists and the two causes do not line up.

  The exemption is per **tag**, not per track: `DIFFERING_TAGS` records
  which tags of each of those 44 tracks differ. Without it a track that
  was exempt because of one tag could differ in another and the check
  would still pass.

The legacy's own tags are recorded in `test/fixtures/legacy_tags.json`,
and `test/test_hierarchy_adapter.py` checks the adapter against them. The
tracks that may differ are named there, and the tracks that may hold
*more* ids than the legacy - the extra ones come after the legacy's, and
are the model's own decision - are named separately.

**What the parity does not establish.** The tests that check the
adapter's *internals* against themselves - the oracle that works out the
expected first level from the relations, and the check that the
model's performance and the adapter's own reading agree - cannot catch
a *misreading of the rule*, because both sides work it out the same
way. They catch the two readings drifting apart, and they catch a
crash. The rule itself is anchored to the legacy's recorded output by
two small fixtures: `partial_order` and `partial_full_first`. A change
to the rule would have to be checked against those.

**The id tags hold a tuple, not a string.** The legacy hands Picard a
tuple of work ids (`parentId = tuple(trackback['id'])`), and Picard
stores a tuple of one id as a bare string and a tuple of more as a list.
So a level of one id reads as a bare id and a level of two reads as a
list of two - which is exactly what the legacy's own tags read as. The
adapter hands Picard the same tuple, so the two agree in the stored
text, not only in the ids. The parity test compares them as Picard
stores them - which normalises a tuple and a list to the same thing, so
it catches a change of the *ids* but not of the container. The
container is checked separately: the adapter hands Picard a tuple, and
a test says so.

Picard joins several values with `'; '` when it writes them out, so a
level of two ids reads as `a; b` in the file. That is why the legacy's
own tags sometimes read that way - it is Picard's list, not a string
the legacy wrote.

Picard's `_set` takes any iterable, so a tuple and a list are stored
the same way: a tuple of two ids is stored as a list of two, and a
tuple of one as a bare id. So the adapter writing a tuple where the
legacy wrote a list (`~cwp_work_top` for a tied top, for instance)
stores exactly the same.

There is no tuple-repr string anywhere: the adapter writes the tuple and
lets Picard store it, so the two cannot drift apart.

The deepest chain in the fixtures is five levels (the Tristan album's
`t2` and `t11`), so the comparison covers `~cwp_workid_0` to
`~cwp_workid_4` and the top. The tags compared are the ones the legacy
wrote for that track *and* the ones the adapter wrote - so a level the
adapter has and the legacy did not would be caught too, and the list of
levels does not have to be kept up to date.

The 13 tracks whose level counts differ are named in the test, and a
test checks that each of them really does differ - so an entry that no
longer needs the exemption fails rather than quietly allowing a
regression. The 44 tracks whose ids differ have the model's own ids
recorded in the fixture and checked - so a wrong id on one of them
would be caught too.

The partly performed works keep their place in the order through the
`Recording.partial` list - the order the relations gave them, not the
work id order.
That order matters in the level *above* the first one. The recording id
stands in for a partly performed work in the first level, so the first
level cannot say where the work stood - and the level above holds the
partly performed works themselves, in the order the relations gave them.
The legacy does the same: a fixture with two partly performed works,
work-zzz first, gives `~cwp_workid_0 = ('rec-1',)` and
`~cwp_workid_1 = ('work-zzz', 'work-aaa')` - not the work id order.

The partly performed works take the *recording id's* place in the level
below, not the front of the level above. A recording that performs
work-a in full and work-b in part, naming work-a first, has a first
level of `('work-a', 'rec-1')` - so work-b goes second.

The level above the first is built by walking the works that lead to
it, in the order the level below gave them, and taking each one's
contribution: a partly performed work contributes itself, and any other
work contributes its parents. The place cannot be counted in the works
that lead to the level - two of them can share a parent and so
contribute one id between them.

Note the model's rule about what a partly performed work contributes:
a work performed *only* in part contributes nothing to the level above
(the recording is not that work), while the legacy puts the work itself
there. That is a difference the model makes on purpose, and it is
tested; the fixtures show the same tags either way.

A work performed partly *and* in full contributes itself, and the model
goes on above it - the work's own parents are reached. The legacy
reaches them too when the recording has another work as well (the
Daphnis tracks), but stops at the work when it is the recording's only
work: the legacy's node for the work holds no parent. That shape is
pinned by `PartialOnlyShapeTestCase`.

**The first relation for a work decides whether the recording stands in
for it.** The legacy reads the attributes of the first relation it finds
for the work (`parse_data` with `'work.id:' + workId`), so:

- a work related **partly first** is partly performed - the recording id
  stands in for it in the first level - and it is in both the partial
  and the complete set, because the legacy adds a second entry for it
  and the work itself still contributes;
- a work related **in full first** is performed in full - the recording
  id does *not* stand in for it, and it is only in the complete set.

A work related partly first and in full afterwards is in both sets, and
so is a work related partly, partly and then in full - the legacy was
run on that case and it writes the work one level up. A work related
**partly twice** is only in the partial set: the recording never
performs it in full.

The `test/fixtures/partial_full_first/` fixture was run through the
legacy: with work-b related in full first and partly second, and
work-a named in between, the legacy writes
`~cwp_workid_0 = ('work-b', 'work-a')` - the work itself, not the
recording id. The `test/fixtures/partial_order/` fixture has the
partial relation first, and the legacy writes
`~cwp_workid_0 = ('rec-1', 'work-a')` there.

**A response with no id is skipped, not raised.** A track with no
recording attached, or a failed lookup, gives a response with no id. It
cannot be tagged, and it must not stop the rest of the release from
being tagged - so the adapter skips it, on both the recording side and
the work side. (A `KeyError` out of the constructor would take the
whole release's tags with it.)

`read_performance` itself raises for a response with no id - its caller
has to filter those out. `AlbumAdapter` does.

The same holds for the rest of the adapter: a chain whose tip the
hierarchy does not hold leaves that one track with no tags, rather
than raising. One bad track must not take the release with it.

The `test/fixtures/partial_order/` fixture was run through the legacy,
and the tags it wrote are the expectations of the tests that use it: with
the recording naming work-b first it wrote
`~cwp_workid_0 = ('rec-1', 'work-a')` and `~cwp_workid_1 = ('work-b',)`,
and with work-a first it wrote `('work-a', 'rec-1')` and `('work-b',)`.

The fixture has two branches - `work-a` goes up to `parent-a` and
`work-b` to `parent-b` - and the legacy's chain has only `work-b`'s.
`parent-a` is in no level of the legacy's tags, and it was run through
the legacy to confirm it: the legacy writes `~cwp_workid_0 =
('work-a', 'rec-1')` and `~cwp_workid_1 = ('work-b',)` and stops there.
So the model reproduces that, and the adapter's own chain is the same
one level further on - the model's own decision, which the fixture
pins.

A work that is performed partly **and** in full contributes *itself* at
the level above the first, and not its parents - it stands in for the
recording id, and the recording id has no parents of its own. The
case where it is also a *leader* of that level was run through the
legacy as well: a recording performing `work-a` in full and `work-b`
partly and then in full, both under `parent-b`, gives
`('work-a', 'rec-1')`, `('work-b',)`, `('parent-b',)` and `('top',)` -
the legacy's own tags, and the adapter's.

Two things the legacy does that the adapter has to reproduce, and that the
review found missing at first:

- **the order of a level reached through an `arrangement` relation.** The
  Tristan album's "Prelude and Liebestod" has no `parts` parent at all -
  it reaches the opera through an `arrangement` relation, and the legacy
  wrote that level's ids in the order the arrangement relation gave them.
  The parents the adapter records are therefore the *resolved* ones - the
  graph's own ordered fallback of `parts`, `arrangement` and `medley` -
  not just the `parts` relations.
- **the name of a top that holds two works with the same title.** The two
  editions of Swan Lake are both called "Swan Lake, op. 20", and the tag
  holds the name once. The tag also holds the *vote winner*, so that
  every track sharing a top names it the same way.

  The vote is per *track*, not per recording: the legacy collects the
  tracks under each top and votes once for every track, so a recording
  that is on two tracks votes twice. The adapter's `multiplicity`
  argument says how many tracks each recording is on. No fixture has a
  recording on two tracks, so the parity is the same either way - but a
  release that has one could give a different winner.

The name tags (`~cwp_work_<n>`) are **not** produced by the adapter. The
legacy strips the parent's text out of the child's name
(`strip_parent_from_work`), which is a separate feature with its own
options and its own tests, and it has nothing to do with the hierarchy.

The comparison above is by the ids the tags hold, not by their literal
text, and the parity test also compares the text as Picard stores it.
The ids and their order are the same either way.

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
  the parent ids. `partof` maps a parent's id tuple to the list of its
  children's id tuples.

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
must not be confused. The second - a recording that performs two works -
is described further down, in *The second fusion*.

* **A work with two parents** (Tristan's "Prelude and Liebestod"). The
  recording performs one work; that work has two parents, so the node one
  level up holds both of them. This is the fusion the model is built
  around: it is what keeps a movement reached by two routes in one place
  in the tree.

  The Liebestod is an *arrangement*, so it has no `parts` parent at all.
  It reaches its two acts through the arrangement relation: "Akt I,
  Vorspiel" (`52836ec3`) and "Excerpt from Tristan und Isolde, WWV 90:
  Akt III" (`eb691aba`). The model's chain for track 2 is

  ```
  Prelude and Liebestod
    -> {Akt I, Vorspiel} + {Excerpt from Akt III}     (one node, both)
      -> Akt III, Szene III
        -> Akt III
          -> Tristan und Isolde, WWV 90
  ```

  which is the legacy's chain exactly.

  **One difference in the tree.** The legacy has only the *fused* node
  `('eb691aba', '52836ec3')`; it has no node for "Akt I, Vorspiel" alone
  and none for "Excerpt from Akt III" alone. The new model has all three:
  the fused node, plus a node for "Akt I, Vorspiel" alone (under "Akt I")
  and a node for "Excerpt from Akt III" alone (under "Akt III, Szene
  III"). The two works each sit in two nodes.

  That is deliberate, and it is what "the tree is a tree, not a chain"
  means. A node is identified by the *set* of works it stands for, so the
  work "Akt I, Vorspiel" is a member of two nodes - the one alone and the
  fused one - and each of those nodes has its own parent. The invariant
  that holds is **every node has at most one parent**, not every work: a
  work may appear in several nodes, because the same work reached by
  different routes needs a container of its own in each route.

  The consequence is that "Akt I, Vorspiel" is listed as a parent of the
  Liebestod through the fused node while also being a movement of "Akt I"
  in its own right. The legacy avoids that by never creating the
  standalone node, so its tree has one route to the Liebestod and one
  route to the Vorspiel. Both trees give the same chain to the track;
  they differ in what the tree looks like above the track.

### The trees differ in more places than the Liebestod
The difference above is not unique to it. The legacy and the new model
agree on 206 of the 218 chains over the fixtures, but not on the shape of
the tree above the track, and they differ in both directions.

* **The new model has more standalone nodes.** A work that is reached by
  two routes gets a node of its own as well as a place in the fused node.
  Tristan's "Akt I, Vorspiel" is one, and Swan Lake's album has fifteen
  of them: the two editions, the eight acts, and the five movements that
  sit under two of them.
* **The new model fuses less.** Don Giovanni's "Recitative and Aria,
  K. 540c" is a standalone work *and* a part of the opera's second act.
  The legacy fuses the two parents into one node; the new model's
  reduction drops the standalone parent - it is a top level work while
  the act is embedded - so there is no fused node at all and the
  standalone work is a root of its own.
* **The new model fuses the top.** Swan Lake's album has a root holding
  both editions, `{11f48c5e, 13867eb1}`, as well as a root for each
  edition alone; the legacy has the two editions as separate roots and
  fuses the Drigo edition with the *original* edition's second act
  (`f9bdafff`) instead.
* **The new model keeps the Gayne scene level**, where the legacy
  flattens the six movements of "Act I, Scene I (Spring)" directly under
  "Act I".

Every one of these is a fusion boundary: the same two works are either
held in one node or in two.

Two of them do change a track's chain. In Swan Lake the five "Danses des
cygnes" movements now route through `{3481d89d, de767453}` and the
original edition's second act - the same reduction that drops a top level
parent in favour of an embedded one as in Don Giovanni - and the one
original-only track ends at the original edition where the legacy wrote
the album-wide Drigo top. The Gayne difference adds the scene level on
purpose. The other seven albums' chains all match the decisions.

### The second fusion
The other thing that puts several works in one node is a **recording that
performs two works** (Planets' track 7, which performs both suites'
"Neptune"). Here the two works are peers.

The legacy fuses the second case as well, and does it by making the two
works a node of their own - the legacy's tree has the key
`('254f397b','4be2ad86')` holding both Neptune works, under the fused key
`('5a75e61c','439c1605')`.

Keeping the two works in separate nodes instead would leave the recording
with two chains rather than one, so a level tag would have to pick one of
them. That is what the model does now. It does not change the *group*,
though: Planets' track 7 has the same group as the other suite tracks
(tracks 1-6), "The Planets, op. 32", because the group is the vote winner
among the track's candidate names. Only track 8, which reaches only the
extension, sits in the "The Planets Suite extension" group.

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
tracked fixtures. There are eighteen of them, and they fall into two groups.
(The untracked `relache` and `walkure` directories are test run artefacts,
not fixtures, and are not counted.)

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
and its parent is the union of the members' parents. Each work contributes
itself - but a work that any 'partial' relation points to contributes the
*recording* id instead, however many relations point at it, and duplicate
ids collapse.

The `partial` attribute and the `live` attribute are separate: Requiem's
track 1 carries two *distinct* works for the same music, one of them
marked 'live', and both are kept as works.

The rule should not assume a pair: a recording may perform more than two
works, and the union of their parents may be several works too.

**The union is kept in the tree, but the group names one of them.**
The legacy's tree key for Requiem's track 1 is the tuple
`('d0be6882-...', '5ba9868e-...')` - both candidates for the level above
the movement - yet `~cwp_workid_1` holds only `d0be6882`.

**Decision: `top_work` may hold several works; `work_group` is the
shuffle key and holds one.** The group is matched by exact string
equality on a *name*, not on an id, so what matters is the name of the
top, not which work id it holds. The two Swan Lake albums make that
clear: the legacy gives them different `~cwp_workid_top` (`13867eb1` for
the complete ballet, `11f48c5e` for the Jarvi release) but the same
`~cwp_work_group`, "Swan Lake, op. 20", for every track of both albums.

The three tags on the Swan Lake album, as the new model would write them
(the legacy gives `~cwp_workid_top` a single id, `13867eb1`, for all 43
tracks):

| tag | d1t1 | d1t18 | d2t2 |
|---|---|---|---|
| `~cwp_workid_top` | `('11f48c5e', '13867eb1')` | `('13867eb1',)` | `('11f48c5e',)` |
| `~cwp_work_top` | `'Swan Lake, op. 20'` | `'Swan Lake, op. 20'` | `'Swan Lake, op. 20'` |
| `~cwp_work_group` | `'Swan Lake, op. 20'` | `'Swan Lake, op. 20'` | `'Swan Lake, op. 20'` |

So the work ids may differ from track to track, and the group name is the
same for all of them. The two editions group together because they carry
the same name, and the new model can keep both ids without affecting the
group.

The group name is the candidate name when the candidates share one name -
no choice needed. Only when the candidates have *different* names does the
group have to pick, and it picks the album-wide vote winner. Measured over
the tracked fixtures, only two tracks have more than one candidate name
at all (nutcracker d1t1 and planets t7) and neither of them ties, so the
tie break does not affect any fixture. The legacy breaks a tie by
lowest normalised name.

Firebird's track 5 used to be a third one. It is not any more. With
`cwp_partial` on the partially performed ballet contributes nothing
upward. With it off the partial attribute is dropped and the ballet
becomes an ordinary complete work - but a work performed in full
contributes its own parents, and the ballet is a top level work with
none, so it contributes nothing upward either way. The track therefore
has one candidate name, the suite.

The legacy narrows a fused top's candidates with three passes, in this
order in `process_album`:

1. `_collapse_fused_top_ids` - keep only the parent ids **common to every
   track** under that top. "The version of the work this release actually
   is": if some track under the top belongs to only a subset of the
   parents, that subset is the release's genuine top work.
2. `_reduce_fused_top_to_highest` - drop a constituent that is a
   **descendant** of another constituent. A top work is by definition the
   highest level work, so "Akt III" fused with the opera it is part of
   becomes just the opera.
3. The **vote by name** that `_select_top_work_names` applies to the
   *names* of the remaining candidates, after which
   `_select_work_group_name` commits the group to a single value.

The new model does the equivalent of (2) - its reduction drops an ancestor
in favour of the more specific descendant, which leaves the same work at
the top. Pass (1) is skipped entirely, and of (3) the vote is kept but
not the collapse: the group name is the vote winner among the track's
candidates, while the candidates stay in place and `~cwp_work_top` keeps
them all. The legacy collapses to one name instead, because its group
has to agree with its top name.

**Measured: the groups match the legacy exactly.** With the recording's
works treated as one node - so a track's candidates are the union of its
works' chain tops - and the group name chosen by the album-wide vote
among those candidates:

| album | legacy | new model |
|---|---|---|
| swanlake_full | 1 group, 43 tracks | 1 group, 43 tracks |
| swanlake_jarvi | 1 group, 19 tracks | 1 group, 19 tracks |
| planets | 6 groups, 12 tracks | 6 groups, 12 tracks |
| requiem | 1 group, 14 tracks | 1 group, 14 tracks |
| nutcracker | 1 group, 24 tracks | 1 group, 24 tracks |
| firebird | 3 groups, 25 tracks | 3 groups, 25 tracks |
| tristan | 6 groups, 11 tracks | 6 groups, 11 tracks |
| gayne | 1 group, 51 tracks | 1 group, 51 tracks |
| daphnis | 1 group, 19 tracks | 1 group, 19 tracks |

Every group set and every track count is identical to the legacy.

### What was built
`hierarchy/album.py` holds the album level model, and `project()` takes the
album's recordings.

`Performance` is one recording's performance of one or more works. It
keeps the works it performs in full and the works it performs only in part
apart, because the two cases differ:

* a work performed in full contributes its own parents to the node's
  parent;
* a work performed both in full and in part contributes itself, because
  the recording id stands in for the work one level below it;
* a work performed only in part contributes nothing, because the recording
  is not that work and does not stand in for it.

The node holds the works themselves, or the recording id for the partially
performed ones.

The `partial` attribute only means anything with the plugin's
`cwp_partial` option on. With it off the attribute is ignored and a
partially performed work is an ordinary one - the legacy's behaviour, and
the one the Gayne album's tags show. `Performance.without_partial()` does
that.

`Album.group_names` picks the shuffle key. Every recording of the album
votes once for each of its *distinct* candidate top names, and a
recording's group is the name with the most votes among its own
candidates. A tie is broken by the lowest normalised name, so every
recording of the album yields the identical string.

**Measured against the legacy, with the fusion in place:**

| album | groups | chains identical | chains matching the decisions |
|---|---|---|---|
| daphnis | identical | 19 / 19 | 19 / 19 |
| firebird | identical | 25 / 25 | 25 / 25 |
| tristan | identical | 11 / 11 | 11 / 11 |
| requiem | identical | 13 / 14 | 14 / 14 |
| nutcracker | identical | 23 / 24 | 24 / 24 |
| planets | identical | 11 / 12 | 12 / 12 |
| swanlake_jarvi | identical | 6 / 19 | 19 / 19 |
| swanlake_full | identical | 8 / 43 | 37 / 43 |
| gayne | identical | 45 / 51 | 45 / 51 |

Three ways of comparing the chains give three figures, so all three are
worth stating:

| comparison | result |
|---|---|
| the work ids at every level are identical | 161 / 218 |
| the work *names* at every level are identical | 162 / 218 |
| the legacy's ids are a subset of the model's, level by level | 206 / 218 |

"chains matching the decisions" is the third of these: a chain matches
when the new model's chain is the legacy's with the top allowed to hold
several works where the legacy picks one.

The chains that differ are:

- **the top holds more works** - 29 of Swan Lake's 43 tracks, 13 of the
  Jarvi release's 19, and one track each of Nutcracker, Planets and
  Requiem. For Swan Lake the top holds both editions of "Swan Lake,
  op. 20", where the legacy names one of them.
- **the Gayne scene level** - six tracks sit one level deeper.
- **a different work** - five Swan Lake tracks (`d1t17`, `d1t19` to
  `d1t22`) now sit under the original edition's Act II rather than the
  Drigo one, and `d2t2` ends at the original edition where the legacy
  wrote the album-wide Drigo top.

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

  The legacy's name selection de-duplicates by name -
  `_select_top_work_names` keeps a name only once, so a fused top holding
  both editions collapses to the single name "Swan Lake, op. 20" before the
  tag is written.

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


## Process: merging stacked PRs

When PRs are stacked (#14 <- #15 <- #16), merging them top-down sends each
merge into its **base branch**, not the trunk: #15 merged into
pr/session-tags, #16 into pr/full-tag-oracle, and only the join commit
brought the content to my-mods. The #15 merge commit (491223f) was bypassed
by #16 and so is not in the trunk history (its tree is identical to
286dcc9, so nothing was lost). Next time: after a lower PR merges, retarget
the next PR to the trunk before merging it, so every merge commit lands on
the trunk in order.

## Remaining migration (after the wiring)

The plugin runs on the package behind `cwp_new_engine` (default
False): `process_album` branches to `_process_album_new_engine`,
which builds the adapter over the raw responses the plugin collected
(`raw_work_responses` in `work_process`, `raw_recordings` in
`add_work_info`), applies the option gates from the user's config,
writes the tags and the annotation presentation, and publishes. The
publish path branches early on the option (version stamp +
`map_tags` + `~cea_works_complete` only) because the legacy's
clear-and-refill assumes intermediates only `extend_metadata` writes.

Every `~cwp_` tag family the legacy writes is in the model, and the
option gates (the genre use flags, `include_workdates`, the key
always/contingent/never fold) are constructor arguments. What the new
engine still does not do - the remaining legacy surface:

- the destination tags (`work`, `part`, `movement`, `groupheading`)
  are left as they are; deriving them from the adapter's tags is the
  `set_metadata`/`extend_metadata` machinery (the diff/medley/title
  variants included). `publish_metadata`'s early branch exists
  precisely so the user's tags are not blanked meanwhile.
- the arrangement/partial/medley name prefixes on `~cwp_work_N` /
  `~cwp_part_N` (the new engine writes the bare names plus the
  annotation parens).
- the orphan tracks' title-derived fallbacks
  (`cwp_derive_works_from_title`).
- the movement numbering (`~cwp_movt_num`/`~cwp_movt_tot`,
  `movement_totals`).
- the early publish branch also skips the fills whose sources the
  adapter already writes - `cwp_top_tag` (`~cwp_work_top`, style,
  grouping), `cwp_work_group_tag` (`~cwp_work_group`), the SongKong
  tags, the iTunes showmovement flags, `ce_version_tag`/
  `cwp_options_tag`, the error/warning rollups, and the
  `~cea_album_composer_lastnames` feed - and tracks the adapter did
  not tag get no `~cea_works_complete`, so the artists section
  readiness gate never opens for them.

The multi-work accumulation diverges from the legacy's last-wins
overwrites for folks/dates - it unions instead - which the parity test
covers on the pinned tracks. The lazy import of the hierarchy package
is forbidden in the plugin module: `sys.modules` holds the first
install's deleted path, so a never-imported submodule cannot be found
through it - import at the module top, next to the session import.
