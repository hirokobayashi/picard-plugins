# Plan: `work_group` tag (single-valued shuffle/grouping key)

## Goal
Decouple two concerns currently conflated in `top_work`:

- **`top_work`** — faithful metadata: *all* genuine top work(s) a track belongs to
  (may be multi-valued when a movement legitimately belongs to several equally
  ranked works, e.g. an overture shared across several opera translations).
- **`work_group`** (new) — a *single, deterministic, album-consistent* value used
  as a grouping/shuffle key (foobar2000 groups by exact string equality on the
  compound pattern, so this must never emit more than one value).

Motivation: the user's foobar2000 shuffle pattern is
`%album artist% | %date% | %album% | %top_work%`; tracks sharing that string are
treated as one unit and not shuffled apart. `top_work` was collapsed to a single
most-voted value purely to serve this, which is lossy. `work_group` takes over the
single-value role so `top_work` can stay faithful.

## What does NOT change (correctness reductions stay)
These remove *wrong* parents and must remain regardless of this feature:
- `_reduce_redundant_parents` — drops an ancestor-of-another parent (Liszt) and a
  standalone parent when an embedded one exists (Don Giovanni).
- `_collapse_fused_top_ids` — collapses a fused top to the ids common to all its
  tracks (Swan Lake).

So the *spurious* parents already fixed never reappear in `top_work`; only
genuinely co-equal top works remain, and only those can make `top_work`
multi-valued.

## Design
- **Value**: the winning top-work **name** (matches current `%top_work%` usage; a
  drop-in). `%album%` is already in the compound key, so cross-album name
  collisions are irrelevant.
- **Single-valued guarantee**: always exactly one value.
  - Unique vote winner → that name.
  - **Tie** → deterministic tie-break (e.g. lowest by `_normalise_name`, else
    first in discovery order) so every track of the group yields the *same*
    single string. This is strictly better than today, where a tie leaks several
    values and sibling tracks can split.
- **`top_work`**: keep all genuine surviving top-work names (multi-value allowed).
- **Internal tags unchanged**: `~cwp_work_N`, the principal `work`, groupheading,
  etc. keep using the single collapsed name (clean hierarchy). Only `top_work`
  becomes potentially multi-valued; `work_group` carries the stable single key.
- **Configurable tag name**: default `work_group`; overridable on the options
  page (like the other tag-name settings). Not gated behind an on/off option on
  this branch unless requested.

## Implementation sketch
1. In `process_album`, after votes and the top collapse, compute a single
   deterministic winner per track's chosen top (reuse `top_name_votes`;
   `_select_top_work_names` already returns winner-or-tied-list — add a
   deterministic reduction of the tie to one value).
2. Write that value to `~cwp_work_group` on each track.
3. Let `top_work` derive from the full genuine name list (stop forcing it to the
   single winner) while `~cwp_work_N` / principal `work` keep the single name.
   - Practically: `_collapse_multiparent_top_name` currently reduces the fused
     `self.parts[topId]['name']` to the winner, which feeds *all* top-derived
     tags. Split this: keep the single value for the hierarchy tags, but expose
     the pre-collapse genuine list to `top_work` specifically.
4. `map_tags` / options: register `crr`/`cwp`-style tag-name setting for
   `work_group`; map `~cwp_work_group` → user tag.
5. Readme: document `work_group`, recommend switching the foobar pattern from
   `%top_work%` to `%work_group%`.

## Tests
- Unit: tie case yields exactly one deterministic `work_group` while `top_work`
  keeps both names.
- Unit: unique winner → `work_group == top_work` (single).
- Integration: an album where `top_work` is genuinely multi-valued (e.g. a
  shared overture) — assert `work_group` is single and identical across the
  shared tracks.
- Regression: existing Swan Lake / Liszt / Don Giovanni fixtures unchanged
  (single top → `work_group == top_work`).

## Open questions / notes
- Grain confirmed: group by the **top work** (whole opera/ballet stays together).
- Name confirmed: **`work_group`**.
- Backward-compat: users keying on single-valued `top_work` should switch to
  `work_group`; note in Readme.
