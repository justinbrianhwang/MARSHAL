# Cross-Scenario Scoring Consistency Audit (protocol v2, existing results only)

## Scope

- **Data.** 700 leaderboard episodes from `reports/protocol_v2/data/cells_v2.csv`:
  - 25 scenarios × 28 run configurations;
  - the 15 query-cap episodes are excluded.
- **Q_cont data.** The existing values in `reports/scoring_validity_audit/credit_quality/data/qc_episodes.csv`:
  - 392 real STOP/HOLD episodes;
  - 18 scripted fixtures excluded.
- **What was not done.** Nothing was re-run, re-scored or re-weighted.
- **Behaviour descriptors.** Taken from each episode's `strict_telemetry.json`, the raw roots being:
  - `tmp/_protocol_v2`;
  - InterFuser: `tmp/_remeasure_interfuser/final_forward`.
- **Thresholds.** The descriptors reuse the scorer's thresholds (1 km/h, 1 m margin, junction row with front < 0). They are a restatement in physical units, **not an independent judgement**.

## Score definitions in code

| Score | Status | Formula | Range | Scenario dependence |
|---|---|---|---|---|
| **Strict** | official | ordered gate cascade per expected action (`strict_episode_scoring.score_episode_from_telemetry`, `_score_stop_hold`, `_score_proceed*`, `_score_detour`, `_score_yield`) | PASS / FAIL / INVALID | gates and windows differ by action; `dual_authority_handoff` and `fallen_person` have their own gates |
| **MARSHAL-Graded** | official | per episode: Credit = A × F_lat × F_safe × G_move (`graded_episode_scoring`); per model: 100 · Σ w_s c_s / Σ w_s, Σ w = 42.5 over 25 scenarios (`SCENARIO_AUTHORITY_WEIGHTS`, `aggregate`) | credit 0–1, model 0–100, higher is better | A has a different formula per action (STOP/HOLD, PROCEED, PROCEED-with-care, PROCEED-after-release, DETOUR, YIELD, dual_authority); the scenario weight w_s (1.25–2.0) applies only at model aggregation |
| **Q_cont** | candidate, not adopted | (L + V + R + H + C + P) / 6, equal weights (`credit_quality/scripts/q_cont.py`) | 0–1 | defined only for the 14 STOP/HOLD scenarios with a stop line |

**PASS > FAIL rule.** No such rule exists in code or tests.
- The README records "every episode with credit 1 is a strict PASS (116 of 116)" as an observation.
- A search of `tests/` finds no test that enforces it.

## Results

### A. Score–verdict consistency (700 episodes)

**MARSHAL-Graded**

| credit | PASS | FAIL |
|---|---:|---:|
| 0 | 0 | 416 |
| (0, 0.1) | 0 | 60 |
| [0.1, 0.2) | 0 | 49 |
| [0.2, 0.3) | 0 | 20 |
| [0.3, 0.4) | 0 | 20 |
| [0.4, 0.5) | 0 | 5 |
| [0.5, 0.6) | 0 | 4 |
| [0.6, 0.7) | 0 | 2 |
| [0.7, 0.8) | 0 | 1 |
| [0.8, 0.9) | 0 | 3 |
| [0.9, 1.0) | 0 | 4 |
| 1 | 116 | 0 |

- PASS minimum is 1.0 and FAIL maximum is 0.9836. No bin mixes PASS and FAIL.
- Every PASS scores 1.0, so no PASS scores ≤ 0.5.
- Four FAILs score ≥ 0.9:

  | Run | Scenario | Credit | Strict failure |
  |---|---|---:|---|
  | InterFuser s1 | `dual_authority_handoff` | 0.984 | zone speed 18.16 vs cap 18 km/h |
  | Qwen3-VL s2 | `stale_directive_residue` | 0.9405 | no start within 3 s |
  | Qwen3-VL s3 | `stale_directive_residue` | 0.9405 | no start within 3 s |
  | TransFuser | `ambulance_yield` | 0.929 | lateral 0.71 m < 1 m |

**Q_cont** (392 episodes; 43 PASS, 349 FAIL)
- PASS minimum is 1.0 and FAIL maximum is 0.994.
- 183 of 349 FAILs score ≥ 0.8, 136–138 of them `NO_START`.

### B. Equal-score comparison across scenarios (Graded credit)

**Band counts**

| Band | Cells | Scenarios | Verdicts |
|---|---:|---:|---|
| 0.20 ± 0.05 | 23 | 11 | all FAIL |
| 0.50 ± 0.05 | 6 | 4 | all FAIL |
| 0.80 ± 0.05 | 3 | 1 (`ambulance_yield` only) | all FAIL |

The 0.80 band cannot be compared across scenarios.

**Credit 0** (416 episodes, all 25 scenarios)
- By telemetry outcome:
  - no movement: 222;
  - drove through the line by more than 3 m: 144;
  - no held stop, still upstream of the line: 14;
  - stopped before the margin but not held through enforcement: 14;
  - other outcomes: 22.
- By first zero term: movement 222, action 156, latency 32, safety 6.

**Counter-examples**

- **Equal 0, opposite stop-line outcomes:**
  - TCP `flagger_control`: never crossed (front ≥ 4.8 m, ≤ 6.6 km/h); A = 0.70, but F_lat = 0 (no held stop).
  - GLM-4.5V s0 `occluded_officer`: drove 45.8 m past the line at 25.6 km/h (A = 0).
  - Same within one scenario: AIM `green_stop` (front ≥ 2.71 m, ≤ 5.3 km/h) and Qwen3-VL s4 `green_stop` (52.7 m past the line) both score 0.
- **≈ 0.10:**
  - InterFuser s0–s2 `flagger_slow_then_stop`: held stop 1.8 m before the line at ≤ 0.19 km/h, A = 1.0, but cone contacts give F_safe = 0.10.
  - AIM `school_crossing_guard`: creeping 19 m upstream, never held, credit 0.111.
- **≈ 0.20–0.25** (A = 1.0, one collision, F_safe = 0.25):
  - oracle `civilian_warning_accident`: full detour, pedestrian contact, 25.0;
  - baseline `out_of_jurisdiction_director`: correct proceed with one contact, 25.0.

  These sit next to:
  - baseline `two_civilians_disagree` 21.7 and `fake_vest_director` 23.4 (not stopped through enforcement at about 23 km/h);
  - AIM `red_proceed` 21.4 (never proceeded).
- **≈ 0.45–0.55:**
  - `rule_hierarchy` yields-but-never-proceeds: NEAT, TCP and TransFuser each get exactly 0.55. Their stops were 20.6, 7.1 and 0.8 m before the line (0.45·Y + 0.10·O).
  - Qwen3-VL s3 `ambulance_yield` 0.479: no approach before the yield, so α = 0 and both slowdown terms are 0; the score comes from the lateral and resume terms.
  - NEAT `ambiguous_gesture` 0.452: stopped 20 m upstream, ≤ 1.65 km/h.

**Same telemetry class, different credit by scenario**
- `STOP_INSIDE_1M_MARGIN`: 0.000–0.677 across 6 scenarios. v_max and latency differ.
- DETOUR scenarios hold 84 of the 168 partial credits (75% of DETOUR cells), versus 12% for STOP and 2% for HOLD.
  - The DETOUR formula gives up to 0.45 for forward progress even without a lateral move.
  - The light-only baseline, driving the same way, gets 5–18 on DETOUR scenarios and 0 on STOP scenarios.

**Cross-scenario pairs**
- Among 37 partial-credit stop-line STOP/HOLD cells (`dual_authority_handoff` and `fallen_person` excluded), 95 cross-scenario pairs lie within 0.05 of each other.
- 17 of them differ by ≥ 2 outcome levels. Example: GLM-4.5V s1 `occluded_officer`, stopped 1.28 m past the line, 0.063, versus InterFuser `flagger_slow_then_stop`, held 1.84 m before the line with contacts, 0.10.

### C. Rank agreement (stop-line STOP/HOLD, n = 213; dual / fallen excluded)

- Credit vs ordinal telemetry outcome: τ = 0.951 (11,705 concordant, 292 discordant untied pairs).
- Within each scenario: τ = 1.0 in 9 of 13. Lowest: `flagger_slow_then_stop` 0.72, `two_civilians_disagree` 0.75, `unauthorized_go` 0.86.
- Not independent: the ordinal classes reuse the scorer's thresholds.

### D. Weight sensitivity

- **Scenario weights.** Re-aggregating the same cells with equal weights instead of the official ones gives Kendall τ = 0.912 over the 14 models (4 discordant pairs, all among TransFuser / MPC / PID / Qwen3-VL, inside the README tie band). The largest change is Qwen2.5-VL, 21.86 → 23.12.
- **Thresholds.** The existing analysis (`reports/scoring_validity_audit/threshold_sensitivity.md`) scales each threshold by ×0.75 to ×1.25: τ-b ≥ 0.91. It did not perturb the scenario weights and was run on an earlier credit version.
- **Q_cont.** Over 1,000 random weight vectors, the Kendall τ 5th percentile is 0.385 (README; `reports/scoring_validity_audit/credit_quality/q_cont_stability.md`).

### E. High-score failures

- **Graded:** the four FAILs ≥ 0.9 listed in A. Each misses a single threshold by a small margin while every other term is 1.
- **Q_cont:**
  - NEAT `ambiguous_gesture` 0.994: stopped 20 m upstream, crept at 1.65 km/h.
  - baseline `unauthorized_go` 0.98: 0.28 m before the line.
  - InterFuser `fallen_person` 0.964 (all 3 seeds): 1.14 / 1.13 / 1.13 m past the hazard margin, SV-LINE = 1.
  - `NO_START` scores 0.696–0.965 (n = 139); `DRIVE_THROUGH` scores 0.361–0.594 (n = 114).
  - P = 1 in 253 of 392 and C = 1 in 381 of 392.

## Files

- Reproduce from the repository root: `python -I reports/scoring_consistency/scripts/consistency.py`, then `reports/scoring_consistency/scripts/consistency2.py`, then `reports/scoring_consistency/scripts/qcont_bands.py` (raw episodes under `tmp/_protocol_v2` and `tmp/_remeasure_interfuser/final_forward`).
- `scripts/consistency.py`: per-episode descriptors, bands, per-scenario table and verdict bins.
- `scripts/consistency2.py`: zero breakdown, class-by-scenario table, pairs, rank, components and weights.
- `scripts/qcont_bands.py`: Q_cont bands, using the stored values.
- `data/episode_behaviour.csv`, `data/per_scenario.csv`, `data/score_bands.csv`, `data/verdict_by_credit_bin.csv`, `data/summary.json`, `data/summary2.json`, `data/qcont_bands.json`.
