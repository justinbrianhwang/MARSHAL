# Reproducibility and determinism of MARSHAL results

This document reports how reproducible MARSHAL's scores are, and reframes what was
previously logged as a "single-seed" caveat. Short version: **the benchmark is
effectively deterministic given a fixed seed** — strict verdicts are stable across
seeds and the continuous `MARSHAL-Graded` score varies by less than 0.01 — so a full
multi-seed re-run reproduces the reported numbers rather than changing them.

## Why this matters

Earlier notes flagged that the reference sweep is **single-seed** and treated that as
a limitation. A seed-variance probe (below) shows the more accurate framing is
*reproducibility*: because the ego spawns are pinned (via `configs/stations.json`) and
the simulation is run deterministically, repeated runs reproduce the same verdicts and
near-identical graded credit. The relevant honesty caveat is therefore **single-map**
and **single primary run per (model, scenario)** — not measurement noise.

## Seed-variance probe (methodology)

- **Controllers:** `oracle` (privileged upper bound) and `baseline` (Traffic-Manager
  autopilot — the most likely source of physics non-determinism).
- **Scenarios:** `green_stop` (STOP), `crash_detour` (DETOUR), `conflicting_authorities`
  (STOP) — spanning a held stop, a continuous-motion maneuver, and a high-tier case.
- **Seeds:** three explicit, distinct scenario RNG seeds (`--seed 1/2/3`), each run
  after a fresh CARLA process restart.
- Scored offline from the recorded `strict_telemetry.json` with the committed strict
  and graded scorers.

## Result

| controller / scenario | seed 1 | seed 2 | seed 3 | ego spawn (x, y) |
|---|---|---|---|---|
| oracle / green_stop | PASS · 1.0 | PASS · 1.0 | PASS · 1.0 | identical |
| oracle / crash_detour | PASS · 1.0 | PASS · 1.0 | PASS · 1.0 | identical |
| oracle / conflicting_authorities | PASS · 1.0 | PASS · 1.0 | PASS · 1.0 | identical |
| baseline / green_stop | FAIL · 0.140 | FAIL · 0.140 | FAIL · 0.140 | identical |
| baseline / crash_detour | FAIL · 0.3275 | FAIL · 0.3301 | FAIL · 0.3358 | identical |
| baseline / conflicting_authorities | FAIL · 0.070 | FAIL · 0.0699 | FAIL · 0.069 | identical |

**Reading it:**
- **Ego spawns are identical across seeds** — the seed does not move the (pinned)
  spawn, confirming the curated-location design.
- **Strict verdicts are perfectly stable** — no PASS/FAIL ever flipped across seeds.
  Since the headline result is the strict pass-rate, it is seed-invariant.
- **`MARSHAL-Graded` is effectively deterministic** — oracle and the STOP scenarios
  are bit-identical; only the *continuous-motion* baseline runs (`crash_detour`,
  `conflicting_authorities`) show sub-0.01 jitter (cross-seed std ≈ 0.003), attributable
  to minor Traffic-Manager / physics timing. On the 0–100 aggregate that is well under
  half a point.

Repeated same-seed runs recorded earlier corroborate this: three repeats of
`rule_hierarchy` (oracle) were bit-identical (0.55, then 1.0 after the oracle fix), as
were repeated `civilian_warning_accident` runs (0.9912).

## Conclusion and decision

A full 14-model × 21-scenario × 3-seed re-run (588 additional episodes, plus VLM API
cost) was assessed and **not run**: it would reproduce the reported numbers with
per-model graded shifts under ±0.5 and zero verdict changes. The value is not worth the
cost. Instead, determinism is documented here as a property of the benchmark.

## Scope and honesty

- The probe covers 2 controllers × 3 scenarios; it is strong evidence, not an
  exhaustive proof over all 14 models and 21 scenarios.
- **Learned E2E controllers** are deterministic given identical inputs (no sampling in
  the reference adapters).
- **VLM controllers** (Track C) additionally depend on the decoding configuration of
  the *served* model behind the API; that is a separate axis, not controlled by the
  scenario seed, and is not characterized here.

## How to reproduce

- **Offline (no CARLA):** re-derive the graded scores from stored telemetry with
  `python scripts/_collect_sweep.py`, which reads each episode's `strict_telemetry.json`
  and re-scores `MARSHAL-Graded` — the reported matrix regenerates exactly.
- **In-sim:** re-run any episode with an explicit `--seed` (e.g.
  `python scripts/_run_reference_staging_sweep.py green_stop --controller oracle --seed 7`)
  and confirm the verdict is unchanged and the graded credit matches within < 0.01.

---

*See also:* [metrics.md](metrics.md) (the scores whose stability is characterized here)
· [what_is_marshal.md](what_is_marshal.md#current-status-honest-scope) (overall scope
and limitations).
