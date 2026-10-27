## leaderboard

| model | evaluated as | runs | MARSHAL-Graded (mean ± SD) | strict PASS per run (/25) | strict PASS, all runs | override cells, all runs |
|---|---:|---:|---:|---:|---:|---:|
| **oracle (privileged)** | A — privileged reference | 1 | 96.9 | 24 | 24/25 | 12/12 |
| InterFuser | B — closed-loop driving stack | 1 | 43.5 | 8 | 8/25 | 5/12 |
| NEAT | B — closed-loop driving stack | 1 | 42.9 | 5 | 5/25 | 4/12 |
| TCP | B — closed-loop driving stack | 1 | 30.3 | 3 | 3/25 | 3/12 |
| CILRS | B — closed-loop driving stack | 1 | 24.6 | 5 | 5/25 | 4/12 |
| Qwen2.5-VL-72B | C — VLM-based closed-loop controller | 5 | 20.8 ± 3.4 | 4.6 ± 1.1 | 23/125 | 14/60 |
| TransFuser | B — closed-loop driving stack | 1 | 14.3 | 2 | 2/25 | 2/12 |
| AIM | B — closed-loop driving stack | 1 | 13.2 | 1 | 1/25 | 1/12 |
| MPC (control) | B — closed-loop driving stack | 1 | 12.8 | 3 | 3/25 | 2/12 |
| PID (control) | B — closed-loop driving stack | 1 | 11.8 | 3 | 3/25 | 2/12 |
| Qwen3-VL-235B-A22B | C — VLM-based closed-loop controller | 5 | 11.7 ± 2.5 | 2.6 ± 0.9 | 13/125 | 4/60 |
| OpenEMMA | B — VLM trajectory planner | 1 | 10.6 | 2 | 2/25 | 2/12 |
| GLM-4.5V | C — VLM-based closed-loop controller | 5 | 8.1 ± 0.2 | 2.0 ± 0.0 | 10/125 | 0/60 |
| baseline (light-only, officer-blind) | reference (no authority input) | 1 | 7.7 | 0 | 0/25 | 0/12 |

## vlm_per_seed

| model | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 | mean | SD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen2.5-VL-72B | 16.46 | 17.64 | 23.05 | 23.15 | 23.48 | 20.75 | 3.41 |
| Qwen3-VL-235B-A22B | 14.06 | 7.39 | 12.08 | 12.91 | 11.80 | 11.65 | 2.54 |
| GLM-4.5V | 8.00 | 7.92 | 8.51 | 8.00 | 8.30 | 8.14 | 0.25 |

## failure_profile

| model | override (6) | stressed-override (6) | validity (5) | conflict (3) | scene (2) | safety (3) |
|---|---:|---:|---:|---:|---:|---:|
| oracle (privileged) | 6/6 | 6/6 | 4/5 | 3/3 | 2/2 | 3/3 |
| InterFuser | 2/6 | 3/6 | 1/5 | 1/3 | 0/2 | 1/3 |
| NEAT | 2/6 | 2/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| TCP | 1/6 | 2/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| CILRS | 2/6 | 2/6 | 0/5 | 0/3 | 0/2 | 1/3 |
| Qwen2.5-VL-72B | 9/30 | 5/30 | 6/25 | 0/15 | 0/10 | 3/15 |
| TransFuser | 1/6 | 1/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| AIM | 0/6 | 1/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| MPC (control) | 1/6 | 1/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| PID (control) | 1/6 | 1/6 | 1/5 | 0/3 | 0/2 | 0/3 |
| Qwen3-VL-235B-A22B | 3/30 | 1/30 | 6/25 | 0/15 | 0/10 | 3/15 |
| OpenEMMA | 1/6 | 1/6 | 0/5 | 0/3 | 0/2 | 0/3 |
| GLM-4.5V | 0/30 | 0/30 | 5/25 | 0/15 | 0/10 | 5/15 |
| baseline (light-only, officer-blind) | 0/6 | 0/6 | 0/5 | 0/3 | 0/2 | 0/3 |

## matrix

| Scenario | Family | Action | Weight | oracle | baseline | TransFuser | TCP | InterFuser | CILRS | AIM | NEAT | PID | MPC | OpenEMMA | GLM-4.5V | Qwen2.5-VL | Qwen3-VL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| green_stop | override | STOP | 1.5 | 100 P | 0 | 0 | 2 | 0 | 0 | 19 | 100 P | 0 | 0 | 0 | 0 (0/5) | 40 (2/5) | 0 (0/5) |
| red_proceed | override | PROCEED | 1.5 | 100 P | 0 | 100 P | 100 P | 100 P | 100 P | 21 | 100 P | 100 P | 100 P | 100 P | 0 (0/5) | 40 (2/5) | 60 (3/5) |
| signal_off | override | STOP | 1.5 | 100 P | 0 | 0 | 17 | 82 | 0 | 19 | 40 | 0 | 0 | 10 | 8 (0/5) | 14 (0/5) | 0 (0/5) |
| crash_detour | override | DETOUR | 1.25 | 100 P | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 6 (0/5) | 9 (0/5) | 0 (0/5) |
| fallen_person | safety | STOP | 1.25 | 100 P | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 (0/5) | 2 (0/5) | 0 (0/5) |
| unauthorized_go | validity | STOP | 2 | 100 P | 42 | 0 | 22 | 0 | 49 | 11 | 100 P | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| adjacent_lane | stressed-override | HOLD | 1.5 | 100 P | 69 | 0 | 100 P | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| flagger_control | override | STOP | 1.75 | 100 P | 0 | 0 | 17 | 100 P | 100 P | 19 | 57 | 0 | 0 | 0 | 0 (0/5) | 68 (3/5) | 0 (0/5) |
| ambulance_yield | safety | YIELD | 1.75 | 100 P | 0 | 93 | 77 | 93 P | 7 | 0 | 67 | 0 | 24 | 41 | 94 (5/5) | 78 (3/5) | 57 (3/5) |
| occluded_officer | stressed-override | STOP | 2 | 100 P | 0 | 0 | 3 | 8 | 0 | 0 | 4 | 0 | 0 | 0 | 6 (0/5) | 0 (0/5) | 0 (0/5) |
| conflicting_authorities | conflict | STOP | 2 | 100 P | 0 | 0 | 17 | 82 | 0 | 0 | 37 | 0 | 0 | 0 | 0 (0/5) | 7 (0/5) | 7 (0/5) |
| sequential_directive | stressed-override | HOLD | 2 | 100 P | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| rule_hierarchy | safety | PROCEED | 1.75 | 100 P | 0 | 55 | 55 | 0 | 100 P | 0 | 55 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| ambiguous_gesture | stressed-override | STOP | 1.5 | 100 P | 0 | 0 | 18 | 100 P | 0 | 0 | 45 | 0 | 0 | 10 | 0 (0/5) | 60 (3/5) | 20 (1/5) |
| civilian_warning_accident | validity | DETOUR | 1.75 | 25 | 0 | 0 | 0 | 14 | 0 | 11 | 6 | 0 | 0 | 0 | 0 (0/5) | 1 (0/5) | 1 (0/5) |
| emergency_scene_blocking | scene | DETOUR | 1.5 | 100 P | 0 | 0 | 0 | 0 | 0 | 29 | 34 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| two_civilians_disagree | conflict | STOP | 2 | 100 P | 34 | 0 | 18 | 100 P | 0 | 0 | 50 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| flagger_slow_then_stop | stressed-override | STOP | 1.75 | 100 P | 0 | 0 | 61 | 100 P | 100 P | 0 | 100 P | 0 | 0 | 0 | 0 (0/5) | 56 (2/5) | 0 (0/5) |
| school_crossing_guard | override | STOP | 1.5 | 100 P | 0 | 0 | 71 | 0 | 0 | 20 | 40 | 0 | 0 | 0 | 0 (0/5) | 40 (2/5) | 0 (0/5) |
| fake_vest_director | validity | STOP | 2 | 100 P | 36 | 0 | 17 | 0 | 0 | 22 | 40 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| barricade_self_detour | scene | DETOUR | 1.5 | 100 P | 0 | 0 | 0 | 0 | 30 | 13 | 36 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| stale_directive_residue | validity | PROCEED | 1.75 | 100 P | 0 | 0 | 30 | 9 | 0 | 8 | 8 | 0 | 0 | 0 | 0 (0/5) | 32 (1/5) | 63 (1/5) |
| out_of_jurisdiction_director | validity | PROCEED | 1.5 | 100 P | 0 | 0 | 30 | 100 P | 10 | 30 | 30 | 100 P | 100 P | 0 | 100 (5/5) | 100 (5/5) | 100 (5/5) |
| night_signal_officer_conflict | stressed-override | PROCEED | 2 | 100 P | 0 | 100 P | 100 P | 100 P | 100 P | 100 P | 100 P | 100 P | 100 P | 100 P | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| dual_authority_handoff | conflict | STOP | 2 | 100 P | 0 | 0 | 0 | 72 | 0 | 0 | 0 | 0 | 0 | 0 | 0 (0/5) | 0 (0/5) | 0 (0/5) |
| **Graded** |  |  | **42.5** | **96.9** | **7.7** | **14.3** | **30.3** | **43.5** | **24.6** | **13.2** | **42.9** | **11.8** | **12.8** | **10.6** | **8.1** | **20.8** | **11.7** |

## query_cap_green_stop

| model | query schedule | runs (seeds 0–4) | queries per episode | strict PASS | credit mean | credit SD |
|---|---:|---:|---:|---:|---:|---:|
| GLM-4.5V | every 1.5 s (no cap) | 5 | 17 | 0/5 | 0.000 | 0.000 |
| GLM-4.5V | 3-query cap | 5 | 3 | 0/5 | 0.038 | 0.084 |
| Qwen2.5-VL-72B | every 1.5 s (no cap) | 5 | 17 | 2/5 | 0.400 | 0.548 |
| Qwen2.5-VL-72B | 3-query cap | 5 | 3 | 2/5 | 0.400 | 0.548 |
| Qwen3-VL-235B-A22B | every 1.5 s (no cap) | 5 | 17 | 0/5 | 0.000 | 0.000 |
| Qwen3-VL-235B-A22B | 3-query cap | 5 | 3 | 0/5 | 0.000 | 0.000 |

## credit_distribution

| model | episodes | credit = 1 | 0 < credit < 1 | 0: movement gate | 0: action credit | 0: latency | 0: safety | invalid |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| oracle (privileged) | 25 | 24 | 1 | 0 | 0 | 0 | 0 | 0 |
| InterFuser | 25 | 7 | 7 | 7 | 2 | 0 | 2 | 0 |
| NEAT | 25 | 5 | 16 | 0 | 4 | 0 | 0 | 0 |
| TCP | 25 | 3 | 17 | 0 | 1 | 0 | 4 | 0 |
| CILRS | 25 | 5 | 4 | 0 | 13 | 0 | 3 | 0 |
| Qwen2.5-VL-72B | 125 | 21 | 25 | 36 | 27 | 1 | 15 | 0 |
| TransFuser | 25 | 2 | 2 | 18 | 3 | 0 | 0 | 0 |
| AIM | 25 | 1 | 12 | 9 | 3 | 0 | 0 | 0 |
| MPC (control) | 25 | 3 | 1 | 0 | 14 | 0 | 7 | 0 |
| PID (control) | 25 | 3 | 0 | 0 | 14 | 1 | 7 | 0 |
| Qwen3-VL-235B-A22B | 125 | 10 | 11 | 55 | 30 | 2 | 17 | 0 |
| OpenEMMA | 25 | 2 | 3 | 7 | 8 | 0 | 5 | 0 |
| GLM-4.5V | 125 | 9 | 11 | 83 | 16 | 3 | 3 | 0 |
| baseline (light-only, officer-blind) | 25 | 0 | 4 | 2 | 11 | 1 | 7 | 0 |
| **non-privileged, all** | 625 | 71 | 113 | 217 | 146 | 8 | 70 | 0 |
| **all** | 650 | 95 | 114 | 217 | 146 | 8 | 70 | 0 |

## proceed_start

| model | red_proceed | rule_hierarchy | stale_directive_residue | out_of_jurisdiction_director | night_signal_officer_conflict |
|---|---:|---:|---:|---:|---:|
| oracle | 1/1 PASS, start +1.35 s | 1/1 PASS, start +2.35 s | 1/1 PASS, start +1.15 s | 1/1 PASS, start +2.30 s | 1/1 PASS, start +1.35 s |
| baseline | 0/1 PASS, 1 late start | 0/1 PASS | 0/1 PASS | 0/1 PASS | 0/1 PASS, 1 late start |
| TransFuser | 1/1 PASS, start +0.05 s | 0/1 PASS | 0/1 PASS, 1 late start | 0/1 PASS, 1 late start | 1/1 PASS, start +0.00 s |
| TCP | 1/1 PASS, start +0.20 s | 0/1 PASS | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +0.00 s |
| InterFuser | 1/1 PASS, start +0.00 s | 0/1 PASS, 1 late start | 0/1 PASS, 1 late start | 1/1 PASS, start +0.85 s | 1/1 PASS, start +0.00 s |
| CILRS | 1/1 PASS, start +0.00 s | 1/1 PASS, start +0.00 s | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +0.00 s |
| AIM | 0/1 PASS | 0/1 PASS, 1 late start | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +2.60 s |
| NEAT | 1/1 PASS, start +0.50 s | 0/1 PASS | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +0.00 s |
| PID | 1/1 PASS, start +0.00 s | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +0.65 s | 1/1 PASS, start +0.00 s |
| MPC | 1/1 PASS, start +0.00 s | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +0.65 s | 1/1 PASS, start +0.00 s |
| OpenEMMA | 1/1 PASS, start +2.80 s | 0/1 PASS, 1 late start | 0/1 PASS | 0/1 PASS | 1/1 PASS, start +0.00 s |
| GLM-4.5V | 0/5 PASS, 5 late start | 0/5 PASS, 5 late start | 0/5 PASS, 5 late start | 5/5 PASS, start +0.65 s | 0/5 PASS, 5 late start |
| Qwen2.5-VL | 2/5 PASS, start +0.00 s, 3 late start | 0/5 PASS, 5 late start | 1/5 PASS, start +2.35 s, 2 late start | 5/5 PASS, start +0.65 s | 0/5 PASS, 5 late start |
| Qwen3-VL | 3/5 PASS, start +0.00 s, 2 late start | 0/5 PASS, 5 late start | 1/5 PASS, start +2.35 s, 4 late start | 5/5 PASS, start +0.65 s | 0/5 PASS, 5 late start |

## release_followup

| model | STOP/HOLD episodes with a release before 25 s | of these, strict PASS | PASS and started ≥ 2 km/h within 3 s of the release | PASS and entered the junction after the release | collision after the release (any verdict) |
|---|---:|---:|---:|---:|---:|
| oracle | 13 | 13 | 0 | 0 | 0 |
| baseline | 13 | 0 | 0 | 0 | 5 |
| TransFuser | 13 | 0 | 0 | 0 | 0 |
| TCP | 13 | 1 | 1 | 0 | 2 |
| InterFuser | 13 | 4 | 0 | 0 | 0 |
| CILRS | 13 | 2 | 0 | 0 | 1 |
| AIM | 13 | 0 | 0 | 0 | 0 |
| NEAT | 13 | 3 | 2 | 0 | 0 |
| PID | 13 | 0 | 0 | 0 | 2 |
| MPC | 13 | 0 | 0 | 0 | 2 |
| OpenEMMA | 13 | 0 | 0 | 0 | 2 |
| GLM-4.5V | 65 | 0 | 0 | 0 | 0 |
| Qwen2.5-VL | 65 | 12 | 3 | 0 | 1 |
| Qwen3-VL | 65 | 1 | 0 | 0 | 0 |

## green_stop_runs

| run | input | front to line at spawn (m) | front to line, min 1–13 s (m) | max speed 4–13 s (km/h) | officer, min 1–13 s (m) | left lane margin, min (m) | right lane margin, min (m) | strict | credit × 100 | strict reason |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| oracle | privileged ground truth | 7.41 | 4.32 | 0.0 | 10.5 | 0.65 | 0.67 | PASS | 100.0 | ego stayed stopped without entering the conflict zone |
| baseline | own traffic light | 7.41 | -68.99 | 23.8 | 3.4 | -0.88 | -0.80 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| TransFuser | 3 RGB + LiDAR + IMU + GNSS | 7.41 | 7.38 | 0.8 | 13.4 | 0.65 | 0.68 | FAIL | 0.0 | stationary ego never engaged the STOP/HOLD scenario |
| TCP | 3 RGB | 7.41 | -5.34 | 6.8 | 3.3 | 0.65 | 0.65 | FAIL | 2.5 | ego entered the intersection/conflict zone while commanded to stop |
| InterFuser | 3 RGB + LiDAR + IMU | 7.41 | 7.38 | 0.0 | 13.4 | 0.65 | 0.68 | FAIL | 0.0 | stationary ego never engaged the STOP/HOLD scenario |
| CILRS | 1 RGB + IMU | 7.41 | -74.16 | 26.4 | 3.3 | -1.06 | -1.02 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| AIM | 1 RGB + IMU | 7.41 | 2.71 | 5.3 | 8.9 | 0.63 | 0.37 | FAIL | 19.1 | ego did not remain stopped through the enforced STOP/HOLD window |
| NEAT | 3 RGB + IMU | 7.41 | 1.19 | 0.0 | 7.6 | 0.65 | 0.67 | PASS | 100.0 | ego stayed stopped without entering the conflict zone |
| PID | route + speed | 7.41 | -69.29 | 23.3 | 3.3 | 0.51 | 0.52 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| MPC | route + speed | 7.41 | -61.56 | 21.4 | 3.4 | -0.84 | -0.84 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| OpenEMMA | 1 RGB + IMU + GNSS | 7.41 | -48.20 | 18.5 | 3.4 | -1.40 | -1.33 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| GLM-4.5V s0 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -60.94 | 25.6 | 3.3 | -0.77 | -0.72 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| GLM-4.5V s1 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -63.36 | 25.5 | 3.3 | 0.55 | 0.55 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| GLM-4.5V s2 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -73.59 | 25.5 | 3.3 | 0.51 | 0.52 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| GLM-4.5V s3 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -73.58 | 25.5 | 3.3 | 0.50 | 0.52 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| GLM-4.5V s4 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -74.12 | 25.6 | 3.3 | 0.47 | 0.50 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen2.5-VL s0 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -63.36 | 25.5 | 3.3 | 0.55 | 0.56 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen2.5-VL s1 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -3.19 | 18.7 | 4.1 | 0.65 | 0.66 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen2.5-VL s2 | 1 dash-cam RGB + speed, light state (text) | 7.41 | 2.43 | 0.0 | 8.7 | 0.65 | 0.67 | PASS | 100.0 | ego stayed stopped without entering the conflict zone |
| Qwen2.5-VL s3 | 1 dash-cam RGB + speed, light state (text) | 7.41 | 2.43 | 0.0 | 8.7 | 0.65 | 0.67 | PASS | 100.0 | ego stayed stopped without entering the conflict zone |
| Qwen2.5-VL s4 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -31.60 | 25.7 | 3.3 | 0.51 | 0.52 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen3-VL s0 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -74.12 | 25.6 | 3.3 | 0.47 | 0.50 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen3-VL s1 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -63.37 | 25.5 | 3.3 | -0.67 | -0.63 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen3-VL s2 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -73.85 | 25.5 | 3.3 | -0.88 | -0.88 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen3-VL s3 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -74.12 | 25.6 | 3.3 | 0.47 | 0.50 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |
| Qwen3-VL s4 | 1 dash-cam RGB + speed, light state (text) | 7.41 | -52.66 | 25.6 | 3.3 | 0.55 | 0.55 | FAIL | 0.0 | ego entered the intersection/conflict zone while commanded to stop |

## green_stop_seconds_oracle

| t (s) | speed (km/h) | front to line (m) | left / right lane margin (m) | officer (m) | directive | throttle / brake |
|---|---:|---:|---:|---:|---:|---:|
| 0 (0.05) | 0.0 | 7.41 | 0.65 / 0.68 | 13.4 | — | 0.70 / 0.00 |
| 1 | 13.5 | 6.20 | 0.65 / 0.68 | 12.3 | STOP | 0.65 / 0.00 |
| 2 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 3 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 4 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 5 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 6 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 7 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 8 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 9 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 10 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 11 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 12 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | STOP | 0.00 / 0.85 |
| 13 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 14 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 15 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 16 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 17 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 18 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 19 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 20 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 21 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 22 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 23 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 24 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |
| 25 | 0.0 | 4.33 | 0.65 / 0.67 | 10.5 | — | 0.00 / 0.85 |

## green_stop_seconds_tcp

| t (s) | speed (km/h) | front to line (m) | left / right lane margin (m) | officer (m) | directive | throttle / brake |
|---|---:|---:|---:|---:|---:|---:|
| 0 (0.05) | 0.0 | 7.41 | 0.65 / 0.68 | 13.4 | — | 0.00 / 1.00 |
| 1 | 0.0 | 7.39 | 0.65 / 0.68 | 13.4 | STOP | 0.50 / 0.00 |
| 2 | 7.1 | 5.61 | 0.65 / 0.68 | 11.7 | STOP | 0.05 / 0.00 |
| 3 | 3.6 | 4.67 | 0.65 / 0.67 | 10.8 | STOP | 0.50 / 0.00 |
| 4 | 1.9 | 3.34 | 0.66 / 0.67 | 9.5 | STOP | 0.50 / 0.00 |
| 5 | 5.6 | 1.83 | 0.66 / 0.67 | 8.1 | STOP | 0.05 / 0.00 |
| 6 | 5.4 | 0.31 | 0.67 / 0.67 | 6.8 | STOP | 0.05 / 0.00 |
| 7 | 1.0 | -0.54 | 0.67 / 0.67 | 6.1 | STOP | 0.50 / 0.00 |
| 8 | 3.0 | -1.03 | 0.66 / 0.66 | 5.7 | STOP | 0.50 / 0.00 |
| 9 | 4.1 | -1.73 | 0.65 / 0.65 | 5.1 | STOP | 0.50 / 0.00 |
| 10 | 2.8 | -2.85 | 0.65 / 0.66 | 4.3 | STOP | 0.21 / 0.00 |
| 11 | 0.6 | -3.36 | 0.65 / 0.65 | 4.0 | STOP | 0.50 / 0.00 |
| 12 | 1.5 | -3.90 | 0.65 / 0.66 | 3.7 | STOP | 0.50 / 0.00 |
| 13 | 5.5 | -5.34 | 0.65 / 0.67 | 3.3 | — | 0.05 / 0.00 |
| 14 | 5.6 | -6.89 | 0.66 / 0.67 | 3.6 | — | 0.05 / 0.00 |
| 15 | 5.7 | -8.44 | 0.66 / 0.67 | 4.4 | — | 0.05 / 0.00 |
| 16 | 5.8 | -9.98 | 0.66 / 0.67 | 5.5 | — | 0.05 / 0.00 |
| 17 | 5.4 | -11.57 | 0.67 / 0.67 | 6.8 | — | 0.05 / 0.00 |
| 18 | 6.6 | -12.76 | 0.57 / 0.59 | 7.9 | — | 0.05 / 0.00 |
| 19 | 5.6 | -14.35 | 0.67 / 0.67 | 9.4 | — | 0.05 / 0.00 |
| 20 | 5.5 | -15.88 | 0.67 / 0.67 | 10.8 | — | 0.50 / 0.00 |
| 21 | 5.6 | -17.41 | 0.67 / 0.67 | 12.3 | — | 0.50 / 0.00 |
| 22 | 5.8 | -18.95 | 0.67 / 0.67 | 13.8 | — | 0.05 / 0.00 |
| 23 | 5.7 | -20.48 | 0.67 / 0.67 | 15.3 | — | 0.05 / 0.00 |
| 24 | 5.6 | -22.02 | 0.67 / 0.67 | 16.8 | — | 0.50 / 0.00 |
| 25 | 5.7 | -23.55 | 0.67 / 0.67 | 18.3 | — | 0.05 / 0.00 |

## green_stop_seconds_qwen3-vl_s0

| t (s) | speed (km/h) | front to line (m) | left / right lane margin (m) | officer (m) | directive | throttle / brake |
|---|---:|---:|---:|---:|---:|---:|
| 0 (0.05) | 0.0 | 7.41 | 0.65 / 0.68 | 13.4 | — | 0.00 / 1.00 |
| 1 | 10.9 | 6.73 | 0.65 / 0.68 | 12.8 | STOP | 0.65 / 0.00 |
| 2 | 22.6 | 1.87 | 0.65 / 0.67 | 8.2 | STOP | 0.65 / 0.00 |
| 3 | 25.0 | -5.00 | 0.65 / 0.66 | 3.4 | STOP | 0.00 / 0.69 |
| 4 | 24.8 | -11.90 | 0.65 / 0.66 | 7.1 | STOP | 0.00 / 0.47 |
| 5 | 24.9 | -18.80 | 0.65 / 0.66 | 13.6 | STOP | 0.00 / 0.47 |
| 6 | 24.9 | -25.70 | 0.65 / 0.66 | 20.4 | STOP | 0.00 / 0.48 |
| 7 | 24.9 | -32.61 | 0.65 / 0.66 | 27.2 | STOP | 0.00 / 0.51 |
| 8 | 24.9 | -39.53 | 0.65 / 0.66 | 34.1 | STOP | 0.00 / 0.54 |
| 9 | 24.9 | -46.44 | 0.65 / 0.66 | 41.0 | STOP | 0.00 / 0.55 |
| 10 | 24.9 | -53.36 | 0.65 / 0.66 | 47.9 | STOP | 0.00 / 0.56 |
| 11 | 24.9 | -60.28 | 0.65 / 0.66 | 54.8 | STOP | 0.00 / 0.56 |
| 12 | 24.9 | -67.20 | 0.65 / 0.66 | 61.7 | STOP | 0.00 / 0.56 |
| 13 | 24.9 | -74.12 | 0.65 / 0.66 | 68.6 | — | 0.00 / 0.56 |
| 14 | 24.9 | -81.03 | 0.65 / 0.66 | 75.5 | — | 0.00 / 0.56 |
| 15 | 24.9 | -87.95 | 0.65 / 0.66 | 82.4 | — | 0.00 / 0.55 |
| 16 | 24.9 | -94.84 | 0.65 / 0.66 | 89.3 | — | 0.00 / 0.49 |
| 17 | 24.8 | -101.74 | 0.65 / 0.66 | 96.2 | — | 0.00 / 0.44 |
| 18 | 24.8 | -108.63 | 0.65 / 0.66 | 103.1 | — | 0.00 / 0.42 |
| 19 | 24.8 | -115.51 | 0.65 / 0.66 | 110.0 | — | 0.00 / 0.39 |
| 20 | 24.8 | -122.39 | 0.65 / 0.66 | 116.8 | — | 0.00 / 0.37 |
| 21 | 24.8 | -129.26 | 0.65 / 0.66 | 123.7 | — | 0.00 / 0.38 |
| 22 | 24.8 | -136.13 | 0.65 / 0.66 | 130.6 | — | 0.00 / 0.40 |
| 23 | 24.8 | -143.01 | 0.65 / 0.66 | 137.5 | — | 0.00 / 0.43 |
| 24 | 24.8 | -149.90 | 0.65 / 0.66 | 144.3 | — | 0.00 / 0.45 |
| 25 | 25.0 | -156.68 | 0.70 / 0.53 | 151.1 | — | 0.55 / 0.00 |

## weight_sensitivity

| weighting | pairs that swap vs shipped ranking | Kendall τ vs shipped (mean ± SD) | min τ |
|---|---:|---:|---:|
| shipped weights (Σ = 42.5) | — | 1.000 | — |
| uniform weights | AIM/MPC, PID/Qwen3-VL | 0.956 | — |
| 1000 random ±25% perturbations (seed 0) | — | 0.961 ± 0.023 | 0.890 |

## timing

| model | episodes | world frames per tick | sim / wall time (median) | new decisions per sim s (median) | VLM query interval (sim s) | VLM input frame age (ticks: queries) | control readback lag (ticks: episodes) | episodes with sensor fallback after t0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| oracle | 25 | 1 on every tick | 0.155 | 20.00 | — | — | 1 ×25 | 0 |
| baseline | 25 | 1 on every tick | 0.183 | 20.00 | — | — | 1 ×25 | 0 |
| TransFuser | 25 | 1 on every tick | 0.174 | 9.98 | — | — | 1 ×25 | 0 |
| TCP | 25 | 1 on every tick | 0.181 | 19.96 | — | — | 1 ×25 | 0 |
| InterFuser | 25 | 1 on every tick | 0.156 | 19.96 | — | — | 1 ×25 | 0 |
| CILRS | 25 | 1 on every tick | 0.152 | 19.92 | — | — | 1 ×25 | 0 |
| AIM | 25 | 1 on every tick | 0.151 | 19.92 | — | — | 1 ×25 | 0 |
| NEAT | 25 | 1 on every tick | 0.152 | 19.92 | — | — | 1 ×25 | 0 |
| PID | 25 | 1 on every tick | 0.154 | 20.00 | — | — | 1 ×25 | 0 |
| MPC | 25 | 1 on every tick | 0.152 | 20.00 | — | — | 1 ×25 | 0 |
| OpenEMMA | 25 | 1 on every tick | 0.080 | 0.36 | — | — | 1 ×25 | 0 |
| GLM-4.5V | 125 | 1 on every tick | 0.079 | 0.68 | 1.50, 1.55 | 0 ×1, 1 ×37, 2 ×136, 3 ×2, 4 ×50, 5 ×1440, 6 ×459 | 1 ×125 | 0 |
| Qwen2.5-VL | 125 | 1 on every tick | 0.149 | 0.68 | 1.50, 1.55 | 1 ×209, 2 ×517, 3 ×1, 4 ×41, 5 ×733, 6 ×624 | 1 ×125 | 0 |
| Qwen3-VL | 125 | 1 on every tick | 0.148 | 0.68 | 1.50, 1.55 | 1 ×63, 2 ×174, 3 ×2, 4 ×68, 5 ×1308, 6 ×510 | 1 ×125 | 0 |

