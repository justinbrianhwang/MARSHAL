# Reproducibility and run-to-run variance of MARSHAL results

This document reports how reproducible MARSHAL's scores are, measured empirically
from **three independent full closed-loop sweeps** (14 models × 21 scenarios each).
Short version: the **privileged oracle and the classical controllers are bit-stable**,
but the **learned E2E controllers carry real run-to-run variance on near-threshold
("borderline") cells** — graded std ranges from ~0 up to ±6.8, and individual
PASS/FAIL verdicts flip between runs. We therefore report `MARSHAL-Graded` as a
**3-run mean ± std** and list the borderline cells explicitly, rather than claiming a
single deterministic number.

> **Correction.** An earlier version of this document claimed the benchmark was
> "effectively deterministic given a fixed seed" and that a multi-seed re-run would
> "reproduce the reported numbers with per-model graded shifts under ±0.5 and zero
> verdict changes." That conclusion was drawn from a probe of only the `oracle` and
> `baseline` controllers, which *are* near-deterministic — it did not generalize to
> the learned models. The 3-run sweep below refutes it: learned controllers show
> per-model graded std up to ±6.8 and verdict flips on borderline cells. The original
> multi-run instinct was correct.

## What varies, and why

- **`oracle` (privileged): zero variance.** Bit-identical graded (100.0 ± 0.0) and
  21/21 strict across all three runs. It reads ground truth and takes a scripted
  action, so there is nothing to perturb — this is why it remains the calibration
  anchor.
- **Classical controllers (`PID`, `MPC`, `TCP`): near-zero.** Graded std ≤ 1.1;
  no verdict flips. Deterministic control laws over pinned spawns.
- **Learned E2E controllers (`TransFuser`, `InterFuser`, `NEAT`, `CILRS`, `AIM`,
  `OpenEMMA`, `baseline`-TM): real variance on borderline cells.** Two compounding
  sources: (1) **GPU/cuDNN non-determinism** in the learned forward pass (non-
  deterministic reductions and cuDNN algorithm selection) nudges control outputs
  run to run; (2) **CARLA physics/Traffic-Manager timing** over a long sweep. Neither
  matters on cells that pass or fail with margin — it only flips cells sitting within
  a hair of the strict threshold. The graded score, being continuous, absorbs the
  same jitter as a few-point std.
- **VLM controllers (Track C): not characterized here.** Each Track-C number is a
  *single API pass* whose per-tick decisions are logged once and then re-scored
  deterministically, so it shows std ≈ 0 in this table — an artifact of single
  sampling, **not** evidence of determinism. The served model's own decoding variance
  is a separate, uncontrolled axis; multi-sample VLM runs are future work.

## Per-model variance across 3 full sweeps

`MARSHAL-Graded` and strict pass-rate as mean ± std over runs 1–3; borderline cells
are (model, scenario) pairs whose strict verdict is not the same in all three runs.

| model | graded (mean ± std) | pass-rate % (mean ± std) | borderline cells (flip across runs) |
|---|---:|---:|---|
| oracle | 100.0 ± 0.0 | 100.0 ± 0.0 | — |
| TransFuser | 55.7 ± 4.9 | 47.6 ± 4.8 | `green_stop`, `unauthorized_go` |
| InterFuser | 53.6 ± 1.5 | 39.7 ± 2.8 | `fallen_person`, `ambulance_yield`, `sequential_directive`, `ambiguous_gesture`, `school_crossing_guard` |
| OpenEMMA-B | 39.5 ± 1.7 | 34.9 ± 2.8 | `signal_off`, `ambiguous_gesture` |
| NEAT | 36.5 ± 6.8 | 23.8 ± 4.8 | `adjacent_lane`, `flagger_control` |
| CILRS | 31.2 ± 2.7 | 17.4 ± 2.7 | `unauthorized_go` |
| AIM | 24.0 ± 2.7 | 15.9 ± 2.7 | `fake_vest_director` |
| baseline (TM) | 23.9 ± 2.1 | 11.1 ± 2.8 | `adjacent_lane` |
| TCP | 14.8 ± 1.1 | 4.8 ± 0.0 | — |
| MPC | 13.4 ± 0.0 | 4.8 ± 0.0 | — |
| PID | 5.8 ± 0.1 | 4.8 ± 0.0 | — |
| Qwen2.5-VL* | 66.2 ± 0.0 | 57.1 ± 0.0 | — (single API pass) |
| Qwen3-VL* | 45.3 ± 0.0 | 38.1 ± 0.0 | — (single API pass) |
| GLM-4.5V* | 33.9 ± 0.0 | 23.8 ± 0.0 | — (single API pass) |

<sub>*Track-C std ≈ 0 is an artifact of single-sample re-scoring, not determinism
(see above). Full per-cell PASS-probabilities across the three runs are in
`outputs/multirun_aggregate.json` (`cell_pass_prob`).</sub>

**Reading it:**
- **Strict pass-rate is not seed-invariant for learned models.** NEAT and TransFuser
  swing by ±5 percentage points of pass-rate between runs; their scenarios-passed
  count moves by ±1–2. This is exactly why the README shows graded as a mean ± std and
  fixes the strict conflict-type profile to one reference run (so its cells sum to
  the scenarios-passed count) rather than averaging fractional PASS counts.
- **The variance concentrates on borderline authority/lane cells** — e.g.
  `unauthorized_go`, `ambiguous_gesture`, `adjacent_lane`, `flagger_control` — cases
  where the correct action sits near a decision boundary the strict scorer thresholds
  on. Cells the model clearly passes or clearly fails never flip.
- **Ranking is mostly stable but not everywhere.** TransFuser and InterFuser lead the
  Track-B field in every run, but their graded means (55.7 vs 53.6) are within
  overlapping error bars — they are a statistical tie for second behind the oracle-free
  leader Qwen2.5-VL. The mid-pack order (OpenEMMA vs NEAT vs CILRS) is where run-to-run
  noise can reshuffle adjacent rows.

## Seed-variance probe (oracle + baseline only) — retained as evidence

The following controlled probe is *consistent* with the picture above: the controllers
it covers (`oracle`, `baseline`) are the near-deterministic end of the spectrum. It is
**not** evidence that the learned models are deterministic — they are not sampled here.

- **Controllers:** `oracle` (privileged upper bound) and `baseline` (Traffic-Manager
  autopilot).
- **Scenarios:** `green_stop` (STOP), `crash_detour` (DETOUR), `conflicting_authorities`
  (STOP).
- **Seeds:** three explicit scenario RNG seeds (`--seed 1/2/3`), each after a fresh
  CARLA restart. Scored offline from recorded `strict_telemetry.json`.

| controller / scenario | seed 1 | seed 2 | seed 3 | ego spawn (x, y) |
|---|---|---|---|---|
| oracle / green_stop | PASS · 1.0 | PASS · 1.0 | PASS · 1.0 | identical |
| oracle / crash_detour | PASS · 1.0 | PASS · 1.0 | PASS · 1.0 | identical |
| oracle / conflicting_authorities | PASS · 1.0 | PASS · 1.0 | PASS · 1.0 | identical |
| baseline / green_stop | FAIL · 0.140 | FAIL · 0.140 | FAIL · 0.140 | identical |
| baseline / crash_detour | FAIL · 0.3275 | FAIL · 0.3301 | FAIL · 0.3358 | identical |
| baseline / conflicting_authorities | FAIL · 0.070 | FAIL · 0.0699 | FAIL · 0.069 | identical |

Ego spawns are pinned (via `configs/stations.json`) and do not move with the seed;
oracle and the held-STOP cells are bit-identical; only the continuous-motion baseline
runs show sub-0.01 jitter. This probe correctly characterizes the *deterministic* end —
its mistake, in the earlier version, was generalizing that to the learned models.

## How to reproduce

- **Full multi-run (in-sim):** `bash scripts/multirun.sh`-style loop — for each run,
  `python scripts/_run_full_sweep.py --no-resume --only <models>` then
  `python scripts/_collect_sweep.py`, snapshotting `outputs/full_sweep_results.json`
  to `outputs/multirun/run_N.json`. Aggregate with
  `python scripts/_aggregate_multirun.py` to regenerate the mean ± std table and
  `outputs/multirun_aggregate.json`.
- **Offline re-score (no CARLA):** `python scripts/_collect_sweep.py` re-derives
  `MARSHAL-Graded` from each episode's stored `strict_telemetry.json`. Offline
  re-scoring of the *same* telemetry is exactly reproducible (zero variance) — the
  run-to-run variance above comes from re-driving the episodes in CARLA, not from
  scoring.
- **Single episode:** `python scripts/_run_reference_staging_sweep.py green_stop
  --controller oracle --seed 7`.

## Scope and honesty

- The 3-run sweep covers all 11 closed-loop models × 21 scenarios; the seed probe
  covers 2 controllers × 3 scenarios. Three runs give a std estimate, not a tight
  confidence interval — treat per-model std as indicative (n = 3).
- **VLM controllers (Track C)** are single-sample here; their API decoding variance is
  uncharacterized and would only widen the error bars.
- Results remain **single-map** (stock Town03); cross-map variance is a separate axis.

---

*See also:* [metrics.md](metrics.md) (the scores whose stability is characterized here)
· [what_is_marshal.md](what_is_marshal.md#current-status-honest-scope) (overall scope
and limitations).
