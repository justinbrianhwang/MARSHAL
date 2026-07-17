# MARSHAL generalization plan — every map, every weather, every time of day

*Master plan for lifting MARSHAL from single-map / single-condition (Town03,
ClearNoon) to the full condition space. This is the roadmap the implementation
work orders are cut from; each phase ships behind an oracle-calibration gate so
the benchmark's anchor (oracle = 100, strict verdicts normative) is preserved at
every step.*

## Why

1. **External validity** — "single synthetic map" is the top limitation named in
   our own docs and the first reviewer objection. Multi-map staging answers it.
2. **R6 (Robustness) becomes measurable** — weather / lighting stress is exactly
   the requirement R6 was defined for and is currently `r_unmeasured`. A
   condition-retention score instruments it without inventing a new taxonomy.
3. **The norm is condition-invariant; perception is not.** An officer's STOP
   binds in rain, fog, and at night. Strict scoring therefore stays *identical*
   across conditions — what the new axes measure is whether models still *read*
   authority under stress. This keeps the science clean: same ground truth,
   harder observation.

## Design invariants (hold in every phase)

- **I1 — Oracle gate.** A (map, condition) cell enters the reported benchmark
  only after the privileged oracle passes every *feasible* scenario there.
  The oracle is condition-blind (privileged), so oracle failures indicate
  *staging* bugs, never model difficulty — the gate keeps staging honest.
- **I2 — Explicit feasibility, never silent.** Not every scenario stages on
  every map (needs a signalized junction, an adjacent lane, a sidewalk offset…).
  Every map ships a machine-readable feasibility mask; reports show `n/feasible`,
  never a silently shrunken denominator.
- **I3 — Default behavior is bit-preserved.** With no new flags, every runner
  stages exactly today's Town03 / default-weather episodes (regression-guarded),
  so all published numbers stay reproducible.
- **I4 — Conditions are logged, not inferred.** Every episode's telemetry header
  records town, weather parameters, and sun state; scoring inputs stay auditable.

## The axes

- **Maps**: enumerate `client.get_available_maps()` at runtime (stock 0.9.16:
  Town01–Town05, Town10HD; AdditionalMaps adds Town06/Town07 and large maps).
  No hardcoded town list anywhere.
- **Weather**: named CARLA presets as the reported grid —
  `ClearNoon` (reference), `WetNoon`, `HardRainNoon`, `FogMorning`,
  `ClearSunset`, `ClearNight` — plus fully parametric
  `WeatherParameters` passthrough for research use.
- **Time of day**: sun altitude/azimuth (noon / sunset / night), reported as part
  of the weather grid (the three `*Noon/*Sunset/*Night` presets) rather than a
  separate axis, to keep the matrix tractable.

## Reporting (two new headline numbers, one existing anchor)

1. **Map-generalization score** — mean graded over feasible scenarios across all
   admitted maps at ClearNoon, per model; plus per-map breakdown.
2. **Condition-robustness score (→ R6)** — on Town03:
   `R6 = mean over conditions c of graded(c) / graded(ClearNoon)`, clamped to
   [0, 1] (a model that *improves* in rain doesn't earn >1). Enters `r_scores`,
   leaves `r_unmeasured` (then only R9 remains unmeasured).
3. **Town03 / ClearNoon** remains the primary leaderboard (continuity with all
   published numbers).

**Matrix control** — the full cross product (21 × ~8 maps × 6 conditions × 14
models) is ~14k episodes and is *not* the protocol. The protocol is two slices:
Axis-A (all maps × feasible scenarios × ClearNoon × all Track-B models) and
Axis-B (Town03 × 21 × 6 conditions × all Track-B models; Track-C VLMs on a
budgeted scenario subset, flagged). Full-cross sampling is future work.

## Phases (each = one Codex work order + a CARLA verification gate run by us)

### P1 — Condition plumbing (offline-testable)
Weather/time as first-class episode config: `cfg["weather"]` (preset name or
parametric dict) applied in `_common.py` before episode start; telemetry header
records town + full weather params; `start.py` / sweep runners get `--weather`
(and `--town` already exists); scoreboard rows carry the condition. Unit tests +
a bit-preservation regression test (no flag → no behavioral change).
**Gate:** full pytest; one CARLA smoke episode per preset shows the weather
actually applied (headers differ, verdict logic untouched).

### P2 — Station finder (per-map staging)
A machine-readable **staging requirement spec** per scenario (needs signalized
junction / run-up meters / sidewalk offset for the officer / adjacent same-road
lane / junction-free stretch / two-lane detour room …), and
`scripts/find_stations.py` that mines a town's topology (waypoints, junctions,
traffic lights) into `configs/stations_<town>.json` + a **feasibility mask**,
with geometric validation checks (spawn clearance, stopline distance bounds,
officer visibility from approach). Town03's generated stations must reproduce
(within tolerance) the hand-curated ones — that's the self-test.
**Gate:** generated stations for two extra towns; we run the oracle there.

### P3 — Multi-map oracle calibration
`scripts/calibrate_town.py`: load town → stations → run the oracle over feasible
scenarios → per-map calibration report (pass/fail per scenario, telemetry
evidence); iterate staging fixes until the oracle clears the mask (I1).
**Gate:** oracle green on ≥3 towns beyond Town03.

### P4 — Robustness suite + R6
Condition-grid runner on Town03; R6 retention computation wired into
`aggregate()` (enters MARSHAL Score via the existing R_WEIGHTS.R6 = 0.02 —
weights unchanged); README/docs updated; sensitivity note (R6's small weight
means the headline barely moves — by design, it's a *report*, not a lever).
**Gate:** oracle retention = 1.0 by construction (privileged); baseline +
TransFuser measured across the 6 presets.

### P5 — Full protocol runs + reporting
Axis-A and Axis-B sweeps, aggregation scripts, README "Generalization" section
with the two new headline numbers, reproducibility doc update (per-axis
variance), landing-page update.

## Risks / honest caveats

- Some towns may not support some scenario families at all (e.g. no signalized
  junctions on rural Town07 stretches) — that is a *finding about the mask*, not
  a failure; reports always show the mask.
- Night/fog may break *officer visibility* enough that Track-B models fail for
  perception reasons — that is precisely what R6 measures, but we must verify
  the officer remains *humanly* visible (headlights/streetlights) so the task
  stays fair; the P4 gate includes a human spot-check of night frames.
- Track-C under all conditions multiplies API cost; Axis-B budgets it and flags
  single-sample cells as before.

*Work orders live in `tmp/` (internal); this plan is the public roadmap. Related:
[reproducibility.md](reproducibility.md) · [evaluation_methodology.md](evaluation_methodology.md).*
