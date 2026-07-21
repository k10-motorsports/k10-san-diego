---
name: track-grading
description: Grade a race track elegantly, subtly and realistically — add corner banking/camber derived from the racing line's own curvature (plus researched known banking) so a flat traced circuit reads like a real one. Use on purpose-built RACETRACKS (road courses, ovals), not public street loops (streets aren't superelevated — they only get real elevation). Vertical elevation comes from real USGS 3DEP (the elevation stage), never invented here.
---

> **k10-san-diego port note:** this repo has no `profile.py` swept-generator consumer for
> `road_profile.cambers` — the loop track is hand-shaped in Blender. Use this script's --dry-run
> output as the camber spec to apply by hand (or via the network builder's `scenery.banking`).

# Track grading

Traced race tracks come out dead-flat: correct in plan, but real circuits have a touch of
**superelevation** — the road leans into the corners — plus wider curbs and the ground's real roll.
This skill adds the *feel* subtly and from data, not by hand.

```bash
python -m scripts.geometry.grade_track project_freeway_net            # write cambers into the config
python -m scripts.geometry.grade_track project_freeway_net --dry-run  # print, don't write
```

## Two separate things — don't confuse them

1. **Vertical elevation = REAL, from USGS 3DEP.** Run the `elevation` stage (+ re-`project`) so the
   centerline Y carries real terrain. `grade_track` does NOT invent vertical relief — it warns if a track
   has no `centerline.elevation.json`. (A track that "feels flat but sits on real ground" already has
   this; a truly flat one is missing the elevation stage — that was Sand Creek's problem.)
2. **Lateral camber = SUBTLE, from curvature.** `grade_track` reads the racing line, finds corners, and
   banks each one a little. This is the part that makes a flat circuit read as real.

## When to use it

- **YES:** purpose-built road courses + ovals (High Plains, Aspen club course, PPIR, IMI, Second Creek).
- **NO:** public **street** loops (Sand Creek). Real streets have ~no superelevation — banking them reads
  wrong. Street tracks get real 3DEP elevation only, no `grade_track`.

## How the camber is derived (procedural, subtle)

- Per-vertex signed curvature from the racing line (turn angle / segment length), smoothed.
- Contiguous runs above a curvature threshold (radius < `r_max_m`, default 260 m) that add up to a real
  corner (`min_corner_deg`, default 24°) become one camber; wiggles and gentle sweepers are ignored.
- `bank_deg = min(max_bank_deg, bank_scale / radius)` — sharper corner ⇒ a little more lean, **capped**
  (defaults 4° / 160, i.e. ~4° at R40, ~2° at R80, ~1° at R160). Signed so the **outside** edge lifts.
  Ramped in/out (`profile.py` `_window`).
- Written to `road_profile.cambers[]`, which `profile.py` already feeds to every swept generator (road,
  shoulder, kerbs, markings) via `bank_at(station)`.

## Researched / known banking

Put verbatim entries in `grading.overrides[]` (same shape as a camber) for banking you *know* from
research — a banked oval, a specific cambered corner — and they're applied instead of the procedural
curve. (PPIR's D-oval is already banked in the k10 pack via the network `scenery.banking` annulus; a
standalone oval would use an override here.) Research each track's real character (onboard laps, track
maps, any laser-scan data) before trusting the procedural default on a distinctive corner.

## Config (`grading` block, all optional)

`max_bank_deg` (4.0) · `bank_scale` (160) · `r_max_m` (260) · `min_corner_deg` (24) · `min_bank_deg` (0.9)
· `bank_sign` (1 — **flip to −1 if the banking comes out inverted**; verify with a render) · `overrides[]`.

## Verify

The bank sign is easy to get backwards. After grading, rebuild + render (or `flythrough`) a corner: the
road should lean INTO the turn (outside edge high). If it leans out, set `grading.bank_sign: -1`.

## Still to add (curbs)

Wider curbs on corner apexes are part of "grading" — extend `kerbs.py` to widen the kerb over the same
corner stations `grade_track` detects (reuse the corner list). Not yet wired.
