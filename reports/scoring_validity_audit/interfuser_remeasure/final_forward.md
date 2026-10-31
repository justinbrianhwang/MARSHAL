# InterFuser final re-measure (forward speed input)

**Run.**
- 25 protocol-v2 scenarios × seeds 0–2 = 75 episodes; all accepted by the published collector, 0 failed.
- Settings pinned and recorded in every episode's metadata: `speed_input` forward, `route_sample_m` 50, `sensor_profile` leaderboard, `step_on_sensor_data` true, `hold_odd_step_control` true (`scripts/run_final_forward.sh`).
- Harness, stations, horizon, staging, weather and light handling are the same as the fixed-3d runs.
- Outputs: `tmp/_remeasure_interfuser/final_forward/interfuser/s{0,1,2}`.

**Scoring.** Credit v2.2-final (`credit_v22_final/scripts/credit_final.py`, unchanged), plus the published v2 credit.

**Data** (`data/`):
- `final_forward_cells.csv`;
- `final_forward_suite_per_seed.csv`, `final_forward_suite_per_setup.csv`;
- `final_forward_vs_fixed3d.csv`;
- `final_forward_flips.csv`;
- `final_forward_repeatability_9.csv`;
- `final_forward_green_stop.csv`.

Script: `scripts/final_forward.py`.

## 1. Suite scores

Strict PASS out of 25; Credit v2.2-final and v2 credit as suite scores.

| setup | seed | strict PASS | Credit v2.2-final | v2 credit |
|---|---:|---:|---:|---:|
| final-forward | 0 | 7 | 36.30 | 34.37 |
| final-forward | 1 | 8 | 44.84 | 42.82 |
| final-forward | 2 | 7 | 38.33 | 37.84 |
| fixed-3d | 0 | 7 | 41.44 | 36.60 |
| fixed-3d | 1 | 7 | 34.15 | 36.41 |
| fixed-3d | 2 | 7 | 35.25 | 36.80 |
| v2 setup | 0 / 1 / 2 | 8 / 7 / 8 | 40.68 / 40.47 / 42.47 | 43.53 / 40.57 / 43.51 |

| setup (seeds 0–2) | strict PASS /25 | Credit v2.2-final, mean ± SD (min–max) | v2 credit, mean ± SD (min–max) |
|---|---|---|---|
| **final-forward** | **7.33 ± 0.58 (7–8)** | **39.82 ± 4.46 (36.30–44.84)** | 38.34 ± 4.25 (34.37–42.82) |
| fixed-3d | 7.00 ± 0.00 (7–7) | 36.95 ± 3.93 (34.15–41.44) | 36.60 ± 0.20 (36.41–36.80) |
| v2 setup | 7.67 ± 0.58 (7–8) | 41.21 ± 1.10 (40.47–42.47) | 42.54 ± 1.70 (40.57–43.53) |

## 2. final-forward vs fixed-3d

**Suite level** (final-forward − fixed-3d):
- Credit v2.2-final: +2.87 points in the mean (39.82 vs 36.95). The seed ranges overlap (36.30–44.84 vs 34.15–41.44), so with three seeds per setup the difference cannot be distinguished from seed-to-seed variation (per-seed differences −5.14, +10.69, +3.08).
- v2 credit: +1.74 (38.34 vs 36.60). The ranges overlap.
- Strict PASS: 22/75 vs 21/75.

**Per scenario** (`data/final_forward_vs_fixed3d.csv`):
- **No cell is separated**: the range-overlap test finds overlap between the three seeds of each setup in every scenario.
- Δ is the mean difference in suite points. S = 3 seeds, P = PASS, F = FAIL.
- Cells whose credits differ between the setups:

| scenario | strict fixed-3d → final | Credit v2.2-final fixed-3d (s0;s1;s2) | final (s0;s1;s2) | Δ (pts) | travel m fixed-3d / final |
|---|---|---|---|---:|---|
| dual_authority_handoff | FFF → FFF | 0.00;0.00;0.00 | 0.00;0.98;0.89 | +2.93 | 114;63;63 / 63;42;11 |
| fake_vest_director | FFF → PPF | 0.00;0.00;0.32 | 1.00;1.00;0.00 | +2.64 | 1;0;10 / 2;10;0 |
| green_stop | FFF → FFP | 0.00;0.00;0.00 | 0.00;0.00;1.00 | +1.18 | 0;0;0 / 0;0;1 |
| conflicting_authorities | FFF → FPF | 0.82;0.00;0.02 | 0.00;1.00;0.00 | +0.25 | 11;41;10 / 41;11;41 |
| school_crossing_guard | FFF → FFF | 0.26;0.16;0.00 | 0.36;0.27;0.00 | +0.25 | 9 / 9 |
| signal_off | PPF → FFF | 1.00;1.00;0.00 | 0.00;0.00;0.00 | −2.35 | 8;8;74 / 71;46;37 |
| two_civilians_disagree | FFP → FFF | 0.77;0.00;1.00 | 0.53;0.37;0.35 | −0.82 | 12;18;12 / 17;10;17 |
| stale_directive_residue | FFF → FFF | 0.30;0.25;0.30 | 0.09;0.10;0.30 | −0.51 | 39;11;11 / 39;9;10 |
| barricade_self_detour | FFF → FFF | 0.12;0.48;0.12 | 0.12;0.12;0.12 | −0.42 | 21 / 21 |
| flagger_slow_then_stop | FFF → FFF | 0.25;0.10;0.10 | 0.10;0.10;0.10 | −0.21 | 24 / 24 |

- The other 15 scenarios differ by at most 0.05 suite points.
- signal_off fails in every forward-speed run (6/6 across this batch and the 9-cell speedfix batch). The ego drives 37–74 m into the junction, against 8 m in the 2 fixed-3d PASS runs. Fixed-3d s0 and s1 passed; fixed-3d s2 failed the same way (about 74 m), which is why the range test counts the cell as overlapping.

## 3. The six earlier speed-input flips: fixed-3d vs speedfix vs final-forward

Credit is Credit v2.2-final.

| cell | fixed-3d | speedfix | final-forward |
|---|---|---|---|
| signal_off s0 | PASS (1.00) | FAIL (0.00) | FAIL (0.00) |
| signal_off s1 | PASS (1.00) | FAIL (0.00) | FAIL (0.00) |
| two_civilians_disagree s2 | PASS (1.00) | FAIL (0.45) | FAIL (0.35) |
| conflicting_authorities s0 | FAIL (0.82) | PASS (1.00) | FAIL (0.00) |
| fake_vest_director s2 | FAIL (0.32) | PASS (1.00) | FAIL (0.00) |
| flagger_slow_then_stop s2 | FAIL (0.10) | PASS (1.00) | FAIL (0.10) |

- The 3 PASS → FAIL flips recur in the final batch.
- The 3 FAIL → PASS flips do not recur.

**Repeatability of the two forward-speed batches on the 9 overlapping cells × 3 seeds** (`data/final_forward_repeatability_9.csv`):
- Strict verdicts agree in **20 of 27** runs.
- The 7 disagreements:
  - conflicting_authorities s0, s1;
  - fake_vest_director s0, s1, s2;
  - flagger_slow_then_stop s2;
  - green_stop s2.
- 9-cell points (Credit v2.2-final, share of the 25-scenario suite):

| batch | s0 | s1 | s2 | mean ± SD |
|---|---|---|---|---|
| speedfix | 7.48 | 11.15 | 11.00 | 9.88 ± 2.08 |
| final-forward | 8.88 | 17.14 | 9.77 | 11.93 ± 4.53 |
| fixed-3d | 13.12 | 4.50 | 6.69 | 8.10 ± 4.48 |

## 4. green_stop

Credit is Credit v2.2-final. Stop position is the front bumper to the stop line.

| setup | seed | strict | credit | travel m | v_max km/h | stop position m | brake at rest (share of rest ticks) |
|---|---:|---|---:|---:|---:|---:|---|
| final-forward | 0 | FAIL (never engaged) | 0 | 0.00 | 0.3 | 7.39 | desired speed 0 (85 %) |
| final-forward | 1 | FAIL (never engaged) | 0 | 0.01 | 1.1 | 7.38 | desired speed 0 (58 %) |
| final-forward | 2 | PASS | 1.00 | 1.21 | 9.0 | 6.18 | desired speed 0 (96 %) |
| fixed-3d | 0 / 1 / 2 | FAIL ×3 | 0 | 0.23 / 0.48 / 0.21 | 3.9 / 7.3 / 3.2 | 6.9–7.2 | desired speed 0 (75–91 %) |

- **The no-start persists:** in 3 of 3 final runs the ego travels ≤ 1.21 m, and is held by the same native-controller branch (desired speed 0 at rest) as in the diagnosis (`../interfuser_greenstop/`).
- **The PASS in seed 2 does not show compliance.** The ego reached 9 km/h over 1.21 m and was then held 6.2 m before the line by the same branch. Its trajectory matches the officer-absent no-start runs (`../interfuser_greenstop/paper_case_green_stop.md`), so the PASS cannot be attributed to the officer.
