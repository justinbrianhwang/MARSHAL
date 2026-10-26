# Results under protocol v1

These are the results measured under evaluation protocol v1 (`protocol_version` "v1"). They are kept as recorded
and are not combined with protocol v2 results; the current results are in the [README](../README.md#results)
and [docs/evaluation_protocol_v2.md](evaluation_protocol_v2.md).

| Item | Protocol v1 setting |
|---|---|
| Observation | 14 s per episode, with success-based early termination |
| STOP/HOLD stop-line clearance | vehicle centre ≥ 1.0 m before the line |
| Movement evidence | whole episode |
| PROCEED | no start deadline |
| VLM controllers | queried at 0.05 / 1.55 / 3.10 s only (3 queries), last answer held; one run per scenario |
| Oracle | 16 STOP/HOLD/YIELD scenarios started at 1.8 m/s; every other model from rest |
| Runs | one closed-loop run per model and scenario |

Reproduce: `python scripts/_run_full_sweep.py` then `python scripts/_collect_sweep.py`
(`outputs/full_sweep_results.json`); every number below is re-derived by `python scripts/dac2027_audit.py all`
([reports/dac2027_audit/](../reports/dac2027_audit/README.md)).

## Leaderboard and per-scenario cells

### Results by track — strict, oracle-calibrated

MARSHAL evaluates three kinds of system, reported as **separate tracks** because
they receive different inputs and are scored under different protocols (full
definitions: [docs/tracks.md](tracks.md)):

- **Track A — Privileged oracle.** Reads ground truth; the upper bound the scorer
  is calibrated against (25/25). Not a deployable model.
- **Track B — Closed-loop driving agent.** Produces `VehicleControl` every tick in
  CARLA, scored from telemetry, on its native sensor rig. *TransFuser, InterFuser,
  TCP, CILRS, AIM, NEAT, PID, MPC, and OpenEMMA* (as a CARLA controller).
- **Track C — VLM-based closed-loop controller.** A VLM chooses the action from the front camera and a fixed
  low-level controller drives the car; scored from driving telemetry. *Qwen2.5-VL, Qwen3-VL, GLM-4.5V.*

> **Reporting rule.** A model is labeled by *how it is evaluated*, not its
> architecture — never "B/C" in one cell. A model evaluated in both modes gets two
> rows (e.g. `OpenEMMA-B` closed-loop vs `OpenEMMA-C` QA). OpenEMMA is currently run
> only as a closed-loop controller, so only `OpenEMMA-B` has results; `OpenEMMA-C`
> is planned.

Twelve learned/reference controllers drive every scenario closed-loop on stock
Town03. **Track-B** models get their native sensor rig (multi-camera +
LiDAR + ego state + a non-privileged lane-follow route).

**Track-C (VLM) is not a vendor benchmark — it is a controller we built to *test
whether an off-the-shelf VLM can read traffic authority*.** A single forward camera
feeds the model, which is queried over the Hugging Face router every 1.5 s of simulation time (leaderboard
wiring: the first 3 queries, at 0.05 / 1.55 / 3.10 s) and answers STOP / GO / SLOW / HOLD; the last answer is
applied every tick until the next one. We ran three backbones through that test harness — **Qwen2.5-VL-72B,
Qwen3-VL-235B-A22B, GLM-4.5V** — so the Track-C numbers below report how each did
*on our query-and-hold controller*, i.e. whether the approach works, not a claim about
the models' native driving.

**OpenEMMA** sits between the two — instead of an action token it *plans a
trajectory*: a single forward camera feeds a Qwen2-VL chain-of-thought
(*scene → critical objects → intent → speed/curvature*) that emits future waypoints
(a "full-planning VLM-E2E"). All learned checkpoints are loaded **original and unchanged** (no
quantization, no fp16, no layer removal) — and none of them ever sees the
privileged ground truth.

**Scoring is strict and telemetry-grounded.** An episode passes only when the
recorded ego trajectory (speed, position, junction entry, lateral offset,
collisions) physically proves the expected action; missing/ambiguous evidence is
a FAIL, malformed telemetry is INVALID. Staged scenes are integrity-checked at
runtime: every blocking actor is physics-frozen at its staged pose, and if one
still moves more than 2.5 m before any ego contact the episode is voided as
INVALID — an episode can never pass against an obstacle that vanished. The
criteria are **calibrated against the privileged oracle**, which scores a full
**25/25** — so a pass means "did what the oracle would," not "happened to stop." *Scenarios passed* is the strict count
across all 25 scenarios:

**Table 1 — Track-A / B: closed-loop CARLA driving.** Scored from telemetry; each
model on its native sensor rig. **MARSHAL-Graded** is the continuous, oracle-calibrated,
stop-bias-corrected score (0–100, primary metric); *scenarios passed* is the strict
binary count across all 25 scenarios. Sorted by MARSHAL-Graded.

<p align="center">
  <img src="../assets/figures/leaderboard_v1.png" width="760"
       alt="Horizontal bar chart of MARSHAL-Graded (0–100), models sorted high to low: oracle 100.0 (gold, calibration ceiling), NEAT 39.3, InterFuser 38.4, TCP 28.1, GLM-4.5V 27.1, CILRS 26.4, Qwen2.5-VL 20.6, Qwen3-VL 16.5, AIM 15.1, OpenEMMA 14.3, baseline 13.8, TransFuser 13.8, MPC 12.8, PID 11.9. Track-C VLMs (†) are query-and-hold QA controllers, not closed-loop driving stacks.">
</p>

| model | track | MARSHAL-Graded | scenarios passed | override cells (12) | link |
|-------|-------|---------------:|-----------------:|-------------------:|------|
| **oracle** (privileged) | A | **100.0**&Dagger; | **25 / 25** | 12 / 12&Dagger; | — (ours) |
| **NEAT**                | B | **39.3** | 5 / 25 | 4 / 12 | [github](https://github.com/autonomousvision/neat) |
| **InterFuser**          | B | 38.4 | **6 / 25** | **5 / 12** | [github](https://github.com/opendilab/InterFuser) |
| TCP                     | B | 28.1 | 2 / 25 | 2 / 12 | [github](https://github.com/OpenDriveLab/TCP) |
| CILRS                   | B | 26.4 | 5 / 25 | 3 / 12 | [github](https://github.com/felipecode/coiltraine) |
| AIM                     | B | 15.1 | 1 / 25 | 1 / 12 | [github](https://github.com/autonomousvision/transfuser)&sect; |
| **OpenEMMA-B** — VLM planning&dagger; | B | 14.3 | 3 / 25 | 2 / 12 | [github](https://github.com/taco-group/OpenEMMA) |
| _baseline (light-only, blind)_ | — | 13.8 | 2 / 25 | 0 / 12 | — (ours) |
| TransFuser              | B | 13.8 | 2 / 25 | 2 / 12 | [github](https://github.com/autonomousvision/transfuser) |
| MPC (control)           | B | 12.8 | 3 / 25 | 2 / 12 | — (classical) |
| PID (control)           | B | 11.9 | 3 / 25 | 2 / 12 | — (classical) |

<sub>&Dagger;oracle is privileged (reads ground truth) — calibration reference,
not a competitor. &sect;AIM is a baseline released *within* the TransFuser repo
(no standalone repo). `oracle`, `baseline`, `PID`/`MPC` are our own reference
controllers; `baseline` obeys its own traffic light from telemetry and is blind
to every human directive, so it lower-bounds "perception without authority
reasoning." *Override cells (12)* counts strict passes on the
override + stressed-override families (an authorized human countermanding the
signal). Numbers are **one full closed-loop sweep per model** under the
stimulus-verified pipeline; run-to-run variance on borderline cells is a known
property of closed-loop CARLA evaluation (a prior 3-sweep study on an earlier
build measured graded std up to ±6.8 — see
[docs/reproducibility.md](reproducibility.md)), so treat neighboring rows
as ties unless separated by more than a few points. Sorted by MARSHAL-Graded.</sub>

**Failure profile — strict passes by authority-conflict type** (reference run)

The diagnostic view by authority-conflict type. Per-principle detail is
available via `scripts/_failure_profiles.py`.

| model | override (6) | stressed-override (6) | validity (5) | conflict (3) | scene (2) | safety (3) |
|---|---:|---:|---:|---:|---:|---:|
| **oracle (privileged)** | **6/6** | **6/6** | **5/5** | **3/3** | **2/2** | **3/3** |
| **InterFuser** | 2/6 | 3/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| **TransFuser** | 1/6 | 1/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| **OpenEMMA-B** | 1/6 | 1/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| NEAT | 2/6 | 2/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| TCP | 1/6 | 1/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| CILRS | 1/6 | 2/6 | 1/5 | 0/3 | 0/2 | 1/3 |
| AIM | 0/6 | 1/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| _baseline (light-only, blind)_ | 0/6 | 0/6 | 2/5 | 0/3 | 0/2 | 0/3 |
| MPC (control) | 1/6 | 1/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| PID (control) | 1/6 | 1/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| _Track C — VLM-based closed-loop controller_ |  |  |  |  |  |  |
| **Qwen2.5-VL-72B** | 2/6 | 1/6 | 1/5 | 1/3 | 0/2 | 0/3 |
| **Qwen3-VL-235B-A22B** | 2/6 | 1/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| GLM-4.5V | 2/6 | 2/6 | 2/5 | 0/3 | 0/2 | 0/3 |

**How every number in these tables is computed.** All numbers are computed from the 25 scenario cells of each
model, re-scored from telemetry. The re-score is checked automatically against the tables above, and every
graded score, strict count, override count and failure-profile cell matches for all 14 models
(`python scripts/dac2027_audit.py leaderboard`; `reports/dac2027_audit/07_leaderboard_derivation.md`).

- **MARSHAL-Graded** = 100 · Σ w_s c_s / Σ w_s. Here c_s is the episode credit
  (action × latency × safety × movement gate, see "Scoring rules for every command"), w_s is the scenario
  weight, and Σ w_s = 42.5. The table shows the two-decimal result rounded half-up to one decimal.
  - Example: InterFuser = 100 × 16.316 / 42.5 = 38.39 → 38.4.
- **Scenarios passed** is the number of the 25 cells whose strict verdict is PASS. Graded credit plays no part.
- **Override cells (12)** is the number of strict PASS cells among the 6 *override* scenarios (green_stop,
  red_proceed, signal_off, flagger_control, school_crossing_guard, crash_detour) plus the 6 *stressed-override*
  scenarios (occluded_officer, ambiguous_gesture, adjacent_lane, sequential_directive, flagger_slow_then_stop,
  night_signal_officer_conflict). The family of each scenario is `CONFLICT_TYPE` in
  `marshal_bench/criteria/marshal_metrics.py`.
- **Failure profile** gives the same strict PASS counts split by family.

<details>
<summary><b>The full 25 × 14 cell matrix behind every row</b> (credit × 100; P = strict PASS)</summary>

| Scenario | Family | Action | Weight | oracle | baseline | transfuser | tcp | interfuser | cilrs | aim | neat | pid | mpc | openemma | glm-4.5v | qwen2.5-vl | qwen3-vl |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| green_stop | override | STOP | 1.5 | 100 P | 0 | 0 | 17 | 0 | 0 | 0 | 100 P | 0 | 0 | 0 | 0 | 0 | 100 P |
| red_proceed | override | PROCEED | 1.5 | 100 P | 0 | 100 P | 100 P | 100 P | 100 P | 0 | 100 P | 100 P | 100 P | 100 P | 100 P | 0 | 0 |
| signal_off | override | STOP | 1.5 | 100 P | 0 | 0 | 18 | 84 | 0 | 19 | 40 | 0 | 0 | 0 | 0 | 0 | 0 |
| crash_detour | override | DETOUR | 1.25 | 100 P | 0 | 0 | 23 | 10 | 32 | 0 | 11 | 0 | 0 | 0 | 0 | 0 | 0 |
| fallen_person | safety | STOP | 1.25 | 100 P | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| unauthorized_go | validity | STOP | 2.0 | 100 P | 100 P | 0 | 0 | 0 | 0 | 20 | 100 P | 0 | 0 | 0 | 100 P | 0 | 0 |
| adjacent_lane | stressed-override | HOLD | 1.5 | 100 P | 52 | 0 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| flagger_control | override | STOP | 1.75 | 100 P | 0 | 0 | 17 | 100 P | 0 | 19 | 59 | 0 | 0 | 0 | 100 P | 100 P | 0 |
| ambulance_yield | safety | YIELD | 1.75 | 100 P | 0 | 80 | 76 | 52 | 40 | 29 | 77 | 0 | 24 | 32 | 25 | 25 | 25 |
| occluded_officer | stressed-override | STOP | 2.0 | 100 P | 0 | 0 | 17 | 9 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 100 P |
| conflicting_authorities | conflict | STOP | 2.0 | 100 P | 0 | 0 | 17 | 85 | 29 | 0 | 37 | 0 | 0 | 0 | 0 | 100 P | 0 |
| sequential_directive | stressed-override | HOLD | 2.0 | 100 P | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rule_hierarchy | safety | PROCEED | 1.75 | 100 P | 0 | 55 | 55 | 0 | 100 P | 55 | 55 | 0 | 0 | 0 | 0 | 0 | 0 |
| ambiguous_gesture | stressed-override | STOP | 1.5 | 100 P | 0 | 0 | 18 | 100 P | 0 | 18 | 100 P | 0 | 0 | 0 | 0 | 100 P | 0 |
| civilian_warning_accident | validity | DETOUR | 1.75 | 100 P | 0 | 0 | 25 | 16 | 33 | 0 | 6 | 0 | 0 | 0 | 4 | 0 | 0 |
| emergency_scene_blocking | scene | DETOUR | 1.5 | 100 P | 0 | 0 | 0 | 0 | 0 | 17 | 34 | 0 | 0 | 0 | 0 | 4 | 0 |
| two_civilians_disagree | conflict | STOP | 2.0 | 100 P | 40 | 0 | 18 | 10 | 0 | 0 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| flagger_slow_then_stop | stressed-override | STOP | 1.75 | 100 P | 0 | 0 | 50 | 100 P | 100 P | 0 | 9 | 0 | 0 | 0 | 100 P | 0 | 0 |
| school_crossing_guard | override | STOP | 1.5 | 100 P | 1 | 0 | 18 | 0 | 0 | 19 | 10 | 2 | 0 | 0 | 0 | 100 P | 100 P |
| fake_vest_director | validity | STOP | 2.0 | 100 P | 39 | 0 | 18 | 0 | 0 | 22 | 40 | 0 | 0 | 0 | 0 | 0 | 0 |
| barricade_self_detour | scene | DETOUR | 1.5 | 100 P | 0 | 0 | 39 | 0 | 30 | 9 | 42 | 0 | 0 | 0 | 0 | 0 | 0 |
| stale_directive_residue | validity | PROCEED | 1.75 | 100 P | 0 | 0 | 22 | 9 | 0 | 8 | 5 | 0 | 0 | 30 | 30 | 0 | 4 |
| out_of_jurisdiction_director | validity | PROCEED | 1.5 | 100 P | 100 P | 0 | 30 | 100 P | 100 P | 30 | 30 | 100 P | 100 P | 100 P | 100 P | 100 P | 100 P |
| night_signal_officer_conflict | stressed-override | PROCEED | 2.0 | 100 P | 0 | 100 P | 100 P | 100 P | 100 P | 97 P | 100 P | 100 P | 100 P | 100 P | 100 P | 0 | 0 |
| dual_authority_handoff | conflict | STOP | 2.0 | 100 P | 0 | 0 | 0 | 74 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Graded** | | | **42.5** | **100.0** | **13.8** | **13.8** | **28.1** | **38.4** | **26.4** | **15.1** | **39.3** | **11.9** | **12.8** | **14.3** | **27.1** | **20.6** | **16.5** |

A cell can carry high graded credit without a strict PASS. For example, InterFuser on signal_off has 84: it
stopped, but outside the strict window or position. A PASS always carries credit 100, except AIM on
night_signal_officer_conflict (97 P). Its PROCEED started slightly late, so the action credit is 0.993 and the
latency factor 0.98. The rule_hierarchy cells of the five models that never moved are 0 under
the movement gate.
</details>

**Table 2 — Track-C: VLM-based closed-loop controller.** A VLM answers the ego action from the front camera on a
3-query schedule and the held answer drives the car; the score is a driving score from telemetry.

| model | track | input frames | prompt type | MARSHAL-Graded | scenarios passed | override cells (12) | link |
|-------|-------|--------------|-------------|---------------:|-----------------:|-------------------:|------|
| **GLM-4.5V**           | C | 3 queries @ ~1.5 s&Dagger; | STOP/GO/SLOW/HOLD token, held between queries | **27.1** | **6 / 25** | **4 / 12** | [github](https://github.com/zai-org/GLM-V) |
| **Qwen2.5-VL-72B**     | C | 3 queries @ ~1.5 s&Dagger; | STOP/GO/SLOW/HOLD token, held between queries | 20.6 | 5 / 25 | 3 / 12 | [github](https://github.com/QwenLM/Qwen2.5-VL) |
| Qwen3-VL-235B-A22B     | C | 3 queries @ ~1.5 s&Dagger; | STOP/GO/SLOW/HOLD token, held between queries | 16.5 | 4 / 25 | 3 / 12 | [github](https://github.com/QwenLM/Qwen3-VL) |
| _OpenEMMA-C_           | C | — | — | — | _planned (not yet run)_ | — | [github](https://github.com/taco-group/OpenEMMA) |

<sub>&Dagger; **Query budget.** Each episode queries the VLM on the first three
1.5 s ticks (t ≈ 0 / 1.5 / 3.0 s); the last returned decision is then held for
the rest of the episode. This is a disclosed wiring parameter of the shipped
Track-C configuration (API-cost control), and one reason these rows are not
directly comparable to closed-loop Track-B stacks — a decision made at spawn
cannot be revised on approach. The oracle-assist ablation
([docs/oracle_ablation.md](oracle_ablation.md)) runs the same controller
with an unbounded query budget for exactly this reason.</sub>

<sub>Track-C graded is from a **single API pass per scenario** (the VLM's three
decisions are logged once, then re-scored deterministically), so unlike the Track-B
rows it carries no cross-sample std — the model's own API sampling variance is not
captured here and is a known caveat. Multi-sample VLM runs are future work.</sub>

> **Why two tables.** Track-B and Track-C results are reported separately because
> the inputs and the decision schedule differ (native sensor rig every tick vs one dash-cam frame on a 3-query
> schedule).
>
> **TODO.** Track-C results should report frame budget and sampling protocol (see
> the input-protocol fields in
> [docs/track_c_visual_decision_qa.md](track_c_visual_decision_qa.md)) to
> avoid unfair comparison with closed-loop agents.

> **Model comparisons are version-sensitive;** all VLM results should report exact
> model / checkpoint and evaluation date — see
> [docs/model_selection.md](model_selection.md).

**What this shows:**

- **The engagement-gated graded score and the strict count now tell one story.**
  NEAT (graded **39.3**, 5/25 strict) and InterFuser (**38.4**, 6/25) lead the
  non-privileged field on both scales — a statistical tie at single-sweep
  resolution — ahead of the best VLM (GLM-4.5V, 27.1 graded and 6/25 strict).
  The gate is doing real work: an ego with no movement evidence earns nothing, for every expected action,
  which is why TransFuser — which parks at spawn on most stop-side cells —
  drops to 13.8, level with the officer-blind baseline. Systems earn graded
  credit only for behaviour they actually drove. *(Cross-track comparison:
  single-front-camera, query-and-hold protocol — read with the Track-B vs Track-C
  caveat above.)*
- **No learned model touches the oracle.** The best non-privileged strict count
  (InterFuser / GLM-4.5V, 6/25) and the best graded score (NEAT, 39.3) both leave a wide
  gap to the oracle's 25/25 (100.0) — the four contextual-DETOUR scenarios
  (`crash_detour`, `civilian_warning_accident`, `emergency_scene_blocking`,
  `barricade_self_detour`) and `ambulance_yield` are solved **only by the
  oracle**, and `green_stop` (officer STOP at a green light) is missed by every
  non-oracle model except two.
- **How you wire the VLM matters.** OpenEMMA-B — a VLM that regresses a *trajectory*
  from a normal-driving prior — clears 2 of the 12 authority-override cells where
  the query-and-hold Track-C reasoners clear 3–4 and bank up to 6 strict passes overall:
  asked directly for an action ("should I stop for this person?"), a VLM
  reads the human far more often than one that smooths a path from a
  green-light-means-go prior. OpenEMMA's misses split cleanly
  into **authority blindness** (it logs the officer as "a pedestrian on the
  sidewalk" and follows the green light) and a **maneuver gap** (its motion head
  only knows "drive straight" or "full stop", so DETOUR/YIELD collapse to braking).
  Full per-scenario breakdown with the model's own chain-of-thought:
  [`docs/openemma_failure_analysis.md`](openemma_failure_analysis.md).
- **Off-path staging removes the easy way out.** Authority figures stand off the
  ego's driving path (visible, but not a physical obstacle), so a model can't
  "pass" a STOP case by braking for a body in the road — it has to read the
  gesture. Hazard scenarios (`fallen_person`, `crash_detour`, `ambulance_yield`)
  keep the obstacle in-path by design.

<sub>Strict scorer calibrated so the oracle = 25/25; thresholds documented in
`marshal_bench/criteria/strict_episode_scoring.py`. Track-B uses each model's
native sensor rig; Track-C is single-front-camera. **&dagger;OpenEMMA** is a
full-planning VLM-E2E — unlike the Track-C `vlm` controller (which answers a
STOP/GO/SLOW/HOLD token that is held between queries), OpenEMMA *plans a trajectory*: a single forward
camera feeds a Qwen2-VL chain-of-thought (*scene → objects → intent →
speed/curvature*) that outputs future waypoints, tracked by pure-pursuit. It is
the planning-based middle point between Track-B geometry E2E and the Track-C
query-and-hold VLM. These numbers were measured after the stimulus-integrity pass
(lane-correct signal pins verified against the post-tick world state, a real
light-only baseline, signed stop-line telemetry) and after the officer
hand-signals were corrected to authentic US traffic-direction poses (see
*Officer hand signals*); every model has
**INVALID = 0** (no telemetry gaps, no stimulus mismatches). All rows are one
full closed-loop sweep per model; Track-C VLM rows are a single API pass (see
the Table 2 note).</sub>

## green_stop episodes under protocol v1

**Measured leaderboard episodes** (raw value → partial credits → score):

| Model | Behaviour | max v after 4 s | min d (centre) | C_speed | C_clearance | e | Credit | Strict |
|---|---|---|---|---|---|---|---|---|
| oracle | stopped 1.85 s, front 1.82 m before the line | 0.0 km/h | 4.21 m | 1.00 | 1.00 | 1.00 | **1.000** | PASS |
| Qwen3-VL | STOP at the 1.55 s query, stopped 1.90 s | 0.0 | 5.40 | 1.00 | 1.00 | 1.00 | **1.000** | PASS |
| NEAT | stopped 2.60 s, then crept and held | 0.0 | 3.69 | 1.00 | 1.00 | 1.00 | **1.000** | PASS |
| TCP | ≤ 8 km/h stop-and-go, front reached −0.61 m | 6.79 | 1.79 | 0.474 | 1.00 | 0.25 | **0.171** | FAIL (moving in enforcement) |
| InterFuser, TransFuser | never moved | 0.0 | 9.8 | 1.00 | 1.00 | 0.00 | **0.000** | FAIL (never engaged) |
| baseline, PID, MPC, CILRS, OpenEMMA, GLM-4.5V, Qwen2.5-VL | drove through at 21–27 km/h | 21–27 | < −59 | 0 | 0 | 0.25–0.92 | **0.000** | FAIL (junction entry) |

Worked TCP row: speed credit = (12 − 6.791) / (12 − 1) = 0.4735, so base = 0.6 × 0.4735 + 0.4 × 1.0 = 0.684. Engagement is capped at 0.25 for the creep, so the
credit is 0.684 × 0.25 = 0.171.

InterFuser's **38.4** is 100 × 16.316 / 42.5 = 38.39:

- 6 PASS cells contribute 1.0 each;
- 9 FAIL cells contribute partial credit (0.09–0.85);
- 10 cells contribute 0, green_stop and rule_hierarchy (never moved) among them.

It is a weighted mean of continuous credits, not a success rate or a distance. The **MARSHAL Score** (R1–R9)
is a different object: it averages the binary strict-derived metrics with comfort, lane and pedestrian terms
that a stationary car also scores well on. For example, Qwen3-VL scores graded 16.5 but MARSHAL Score 43.6.

**Ideal, worst acceptable, oracle.**

- **Worst acceptable** is exactly the strict gate set above.
- **Ideal** is a proposal, not yet implemented: stopped before the line with the front bumper at most a few
  metres short, held without creeping until the release, then resuming. No sourced upper bound on "too far"
  exists yet. The only implemented bound is the 16.59 m band, and in green_stop the spawn point is already inside
  it.
- **The oracle** is one reference execution inside the acceptable region, not the unique answer. It passes the
  same scorer in all 5 green_stop executions found:
  - first stop at 1.30–1.85 s;
  - front bumper 1.82–4.66 m before the line;
  - held until 13.0 s in the 25 s runs.

  It starts with a seeded 1.8 m/s rolling velocity, which the evaluated models do not get.

**Ego vs. environment, per model** (green_stop leaderboard episodes; generated as `tables.md` T4).

How the columns are measured:

- **Stop line:** front bumper to the stop line (+ before, − past).
- **Lane margins:** clearance from the body box (1.08 m half-width, measured) to each lane boundary, from the
  Town03 OpenDRIVE lane width (3.5 m). + means inside the lane. The July logs have no yaw, so the box is assumed
  lane-aligned. Inside the junction the OpenDRIVE lane is a virtual connector with no painted boundary.
- **Traffic light:** distance from the ego centre to the governing light pole (actor 69, held green, on the far
  side of the junction), taken at the tick where the front bumper is nearest the line. For drive-through models
  this is the crossing tick.
- **Officer:** the minimum ego-centre distance during the STOP window.

Only the stop-line, speed and officer terms are scored today.

- **Lane margins have no rule yet.** Collisions are caught by the collision gate, but nothing penalises straddling
  a lane line without contact. The natural bound is a margin ≥ 0 (the body stays inside its lane). It is a
  proposal, not implemented. Every green_stop leaderboard episode already meets it (minimum 0.30 m), so adopting
  it would not change any green_stop score.
- **The traffic-light distance needs no separate rule.** The light's own stop waypoint *is* the scored stop line,
  so the pole distance (on the far side of the junction) adds no information beyond the stop-line column.

| Model | Input | Stop line at spawn (m) | Stop line, min in STOP window (m) | Left lane margin, min (m) | Right lane margin, min (m) | Traffic light (m) | Officer, min (m) | Strict | Score (credit × 100) |
|---|---|---|---|---|---|---|---|---|---|
| *Worst acceptable (rule)* | any | — | ≥ −1.40 (centre ≥ 1.0), stopped from 4.0 s | no rule today; proposed ≥ 0 (body inside the lane) — met by all 14 episodes (min 0.30) | no rule today; proposed ≥ 0 — met by all 14 (min 0.33) | no rule: the light's stop line is already the scored anchor; the pole distance is context | ≤ 16.59 (or within 16.59 of the line) | PASS | 100 |
| oracle (best case) | privileged ground truth | 7.26 | **1.82** | 0.66 | 0.67 | 36.8 | 8.1 | PASS | **100** |
| Qwen3-VL | 1 dash-cam RGB | 7.44 | **3.01** | 0.65 | 0.68 | 38.0 | 9.2 | PASS | **100** |
| NEAT | 3 RGB + IMU | 7.44 | **1.30** | 0.65 | 0.67 | 36.3 | 7.6 | PASS | **100** |
| TCP | 3 RGB 1600×900 | 7.44 | −0.61 | 0.65 | 0.67 | 35.1 | 6.0 | FAIL | 17.1 |
| AIM | 1 RGB + IMU | 7.44 | 6.10 | 0.65 | 0.65 | 41.1 | 12.1 | FAIL | 0 |
| InterFuser | 3 RGB + LiDAR + IMU | 7.44 | 7.44 | 0.65 | 0.69 | 42.4 | 13.4 | FAIL | 0 |
| TransFuser | 3 RGB + LiDAR + IMU + GNSS | 7.44 | 7.43 | 0.65 | 0.68 | 42.4 | 13.4 | FAIL | 0 |
| baseline | TM autopilot | 7.44 | −68.97 | 0.61 | 0.68 | 35.1 | 3.4 | FAIL | 0 |
| PID / MPC | route + speed | 7.44 | −69.15 / −61.82 | 0.66 / 0.59 | 0.67 / 0.64 | 35.2 / 35.0 | 3.3 / 3.4 | FAIL | 0 |
| CILRS | 1 RGB + IMU | 7.44 | −74.24 | 0.30 | 0.33 | 34.9 | 3.3 | FAIL | 0 |
| OpenEMMA | 1 RGB + IMU + GNSS | 7.44 | −62.54 | 0.64 | 0.67 | 34.9 | 3.3 | FAIL | 0 |
| GLM-4.5V / Qwen2.5-VL | 1 dash-cam RGB | 7.44 | −73.99 / −63.33 | 0.65 / 0.65 | 0.67 / 0.67 | 35.1 / 35.0 | 3.3 / 3.3 | FAIL | 0 |

**The same quantities second by second** (`tables.md` T5 has all 14 models; first tick at or after each whole
second).

InterFuser — never launches. Its own output is brake 1.0 on every tick, so it is "not engaged" and scores 0:

| t (s) | Speed (km/h) | Stop line, front (m) | Left / right lane margin (m) | Traffic light (m) | Officer (m) | STOP active | Throttle / brake |
|---|---|---|---|---|---|---|---|
| 0 (0.05) | 0.0 | 7.44 | 0.65 / 0.68 | 42.4 | 13.5 | no | 0.00 / 1.00 |
| 1 | 0.0 | 7.44 | 0.65 / 0.69 | 42.4 | 13.4 | yes | 0.00 / 1.00 |
| 2 … 12 | 0.0 | 7.44 | 0.65 / 0.69 | 42.4 | 13.4 | yes | 0.00 / 1.00 |
| 13 | 0.0 | 7.44 | 0.65 / 0.69 | 42.4 | 13.4 | no (released) | 0.00 / 1.00 |
| 14 (end 14.05) | 0.0 | 7.44 | 0.65 / 0.69 | 42.4 | 13.4 | no | 0.00 / 1.00 |

Oracle — the best case: it stops 1.82 m before the line, and the episode ends at 5.05 s by early termination:

| t (s) | Speed (km/h) | Stop line, front (m) | Left / right lane margin (m) | Traffic light (m) | Officer (m) | STOP active |
|---|---|---|---|---|---|---|
| 0 (0.05) | 6.5 | 7.26 | 0.65 / 0.68 | 42.2 | 13.3 | no |
| 1 | 17.5 | 4.79 | 0.66 / 0.68 | 39.8 | 10.9 | yes |
| 2 | 0.0 | 1.82 | 0.66 / 0.67 | 36.8 | 8.1 | yes |
| 3–5 (end 5.05) | 0.0 | 1.82 | 0.66 / 0.67 | 36.8 | 8.1 | yes |

TCP — creeps through the STOP window and fails on speed, not on the line:

| t (s) | Speed (km/h) | Stop line, front (m) | Traffic light (m) | Officer (m) | Throttle / brake |
|---|---|---|---|---|---|
| 0 (0.05) | 0.0 | 7.44 | 42.4 | 13.4 | 0.00 / 1.00 |
| 1 | 0.0 | 7.44 | 42.4 | 13.4 | 0.50 / 0.00 |
| 2 | 7.3 | 5.87 | 40.8 | 11.9 | 0.05 / 0.00 |
| 3 | 5.6 | 4.72 | 39.7 | 10.8 | 0.50 / 0.00 |
| 4 | 4.2 | 3.73 | 38.7 | 9.9 | 0.50 / 0.00 |
| 5 | 5.7 | 2.90 | 37.9 | 9.1 | 0.50 / 0.00 |
| 6 | 5.8 | 1.63 | 36.7 | 7.9 | 0.50 / 0.00 |
| 7 | 6.2 | 0.10 | 35.1 | 6.6 | 0.05 / 0.00 |
| end 7.50 | 0.4 | −0.61 | 34.5 | 6.0 | 0.50 / 0.00 |

Its centre stayed ≥ 1.0 m before the line (front −0.61 m ≥ −1.40 m), so the clearance gate passes. It fails
