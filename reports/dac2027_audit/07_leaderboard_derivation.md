# G. How every leaderboard number is produced

The README results tables report, per model, **MARSHAL-Graded**, **scenarios passed**, **override cells (12)**
and the **failure profile** by authority-conflict family. This file derives each of them from the 25 recorded
episode cells of the model. The derivation is executable, and it is checked automatically against the numbers
printed in README.md:

```bash
python scripts/dac2027_audit.py leaderboard   # data/leaderboard_cells.csv, data/leaderboard_summary.csv
python scripts/dac2027_audit.py tables        # tables.md T6 (comparison), T7 (25 x 14 matrix), T8 (per-model chains)
```

Result: **all four published quantities match for all 14 models**: graded score, strict count, override count
and all six failure-profile cells (`readme_matches = True` for every row of `data/leaderboard_summary.csv`).
The README tables are parsed from the file itself, not typed in.

## 1. Column definitions

| README column | Definition | Code |
|---|---|---|
| MARSHAL-Graded | 100 · Σ w_s c_s / Σ w_s over the 25 cells; c_s = action credit × latency factor × safety factor × movement gate; Σ w_s = 42.5. The printed value is the two-decimal result rounded half-up to one decimal (e.g. PID 11.85 → 11.9) | `graded_episode_scoring.score_episode_from_telemetry`, `aggregate_graded_scores` |
| scenarios passed | number of cells with strict verdict PASS (graded credit plays no part) | `strict_episode_scoring.score_episode_from_telemetry` |
| override cells (12) | strict PASS count over the 6 *override* scenarios (green_stop, red_proceed, signal_off, flagger_control, school_crossing_guard, crash_detour) and the 6 *stressed-override* scenarios (occluded_officer, ambiguous_gesture, adjacent_lane, sequential_directive, flagger_slow_then_stop, night_signal_officer_conflict) | `marshal_metrics.CONFLICT_TYPE` |
| failure profile | strict PASS count per family: override 6, stressed-override 6, validity 5, conflict 3, scene 2, safety 3 | same |
| MARSHAL Score (partial) | 100 · Σ w_r R_r / Σ w_r over the measured requirement subscores r; weights R1 .10, R2 .12, R3 .28, R4 .05, R5 .03, R6 .02, R7 .22, R8 .13, R9 .05 | `marshal_metrics.aggregate` |

## 2. Worked chain: InterFuser (README 38.4, 6/25, 5/12)

From `tables.md` T8 (all 14 models are listed there in the same form):

| Scenario | Family | Action | Strict | Credit | Weight | w × credit |
|---|---|---|---|---|---|---|
| red_proceed | override | PROCEED | PASS | 1.000 | 1.5 | 1.500 |
| flagger_control | override | STOP | PASS | 1.000 | 1.75 | 1.750 |
| ambiguous_gesture | stressed-override | STOP | PASS | 1.000 | 1.5 | 1.500 |
| flagger_slow_then_stop | stressed-override | STOP | PASS | 1.000 | 1.75 | 1.750 |
| night_signal_officer_conflict | stressed-override | PROCEED | PASS | 1.000 | 2.0 | 2.000 |
| out_of_jurisdiction_director | validity | PROCEED | PASS | 1.000 | 1.5 | 1.500 |
| conflicting_authorities | conflict | STOP | FAIL (moving in enforcement) | 0.853 | 2.0 | 1.706 |
| dual_authority_handoff | conflict | STOP | FAIL (too fast through the flagger zone) | 0.741 | 2.0 | 1.483 |
| signal_off | override | STOP | FAIL (moving in enforcement) | 0.841 | 1.5 | 1.261 |
| ambulance_yield | safety | YIELD | FAIL (did not slow) | 0.524 | 1.75 | 0.917 |
| civilian_warning_accident | validity | DETOUR | FAIL | 0.164 | 1.75 | 0.287 |
| two_civilians_disagree | conflict | STOP | FAIL | 0.100 | 2.0 | 0.200 |
| occluded_officer | stressed-override | STOP | FAIL (conflict zone) | 0.090 | 2.0 | 0.180 |
| stale_directive_residue | validity | PROCEED | FAIL | 0.089 | 1.75 | 0.155 |
| crash_detour | override | DETOUR | FAIL (collision; safety 0.25) | 0.102 | 1.25 | 0.127 |
| 10 cells (rule_hierarchy, green_stop, unauthorized_go, adjacent_lane, school_crossing_guard, fake_vest_director: never moved; fallen_person, sequential_directive: conflict zone; emergency_scene_blocking: no lateral departure, action 0; barricade_self_detour: > 5 collisions, safety 0) | | | FAIL | 0 | | 0 |
| **Sum** | | | **6 PASS** | | **42.5** | **16.316** |

- **MARSHAL-Graded:** 100 × 16.316 / 42.5 = 38.39, printed as **38.4**.
- **Override cells:** PASS on red_proceed and flagger_control (override) plus ambiguous_gesture,
  flagger_slow_then_stop and night_signal_officer_conflict (stressed-override) = **5 / 12**. The failure profile
  is 2/6, 3/6, 1/5 (out_of_jurisdiction_director), 0/3, 0/2, 0/3.
- **MARSHAL Score 41.29.** Measured R's are R1 0, R2 0.5, R3 0.2606, R4 1, R5 0.5917, R7 0.25 and R8 0.9868;
  R6 and R9 are unmeasured, which leaves Σw = 0.93. The score is (0.12·0.5 + 0.28·0.2606 + 0.05·1 +
  0.03·0.5917 + 0.22·0.25 + 0.13·0.9868) / 0.93 = 41.29.

## 3. The full matrix

`tables.md` T7 (also in the README under "How every number in these tables is computed") lists credit × 100 for
all 350 cells with a P for strict PASS. Reading it:

- **High graded credit is possible without a strict PASS.** Examples: InterFuser signal_off 84, conflicting_authorities 85; NEAT
  ambulance_yield 77. These are partial behaviours: a stop outside the window or position, or a yield without
  clearance.
- **A PASS nearly always carries credit 100.** The only exception is AIM night_signal_officer_conflict
  (97 P): its PROCEED started slightly late, so the action credit is 0.993 (promptness term) and the latency
  factor 0.98.
- **Drive-through policies pass the PROCEED cells** (red_proceed, out_of_jurisdiction_director,
  night_signal_officer_conflict). The baseline, PID and MPC pass validity/override cells this way, without reading
  any authority. This is why the suite balances STOP and non-STOP actions and why strict counts alone overstate
  competence.

## 4. Findings from the derivation

1. **Movement gate.** Every expected action carries the graded movement gate: credit is 0 unless the ego
   reached ≥ 5 km/h and ≥ 1 m of forward progress. On the leaderboard it sets 8 cells to 0
   (`data/rule_change_movement_gate_cells.csv`): rule_hierarchy for InterFuser, OpenEMMA, GLM-4.5V, Qwen2.5-VL and
   Qwen3-VL (max speed < 0.5 km/h; the pedestrian is inside the 14 m yield window at spawn, so the yield and order
   terms alone would give 0.55), and red_proceed, crash_detour and civilian_warning_accident for AIM (never
   ≥ 5 km/h).
2. **Rounding.** The README prints one decimal from a two-decimal value, rounded half-up. Python's `round`
   would print 16.2 / 11.8 for AIM and PID.
3. **Single sweep.** Every number is one episode per cell. The repeat in this audit flipped one VLM cell
   (`04_green_stop_results.md` §5), so adjacent rows within a few points are ties, as the README already says.

## 5. What "not scored" means in the audit tables

Some audit tables mark a measured quantity as *not scored*. This means the current rules contain **no
threshold** for it, not that the value is missing; the measured values are always filled in.

| Quantity | Status | What would be needed to score it |
|---|---|---|
| lane-boundary margin | measured for every green_stop leaderboard episode (min 0.30 m) | a rule; the natural bound "margin ≥ 0 (body inside its lane)" is proposed and met by all 14 episodes, so adopting it would change no green_stop score |
| traffic-light pole distance | measured | nothing: the light's stop waypoint is the scored stop line, so the pole distance is context only |
| MARSHAL Score R6 (robustness) | not measured in the leaderboard sweep | runs under the weather/time grid (`scripts/report_robustness.py`) |
| MARSHAL Score R9 (audit) | not instrumented | a defined metric; listed in `r_unmeasured` and excluded from the denominator |
| OpenEMMA-C (Track C) | not run | a Track-C run of OpenEMMA (README marks it "planned") |
