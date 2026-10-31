# InterFuser multi-seed re-measurement

**What was compared.** Two adapter setups, each run on the 25 protocol-v2 scenarios under v2 conditions (Town03 stations, 20 Hz, 25 s horizon, same staging):

| setup | adapter configuration | runs |
|---|---|---|
| **v2 setup** | as on the v2 leaderboard: `route_sample_m 0`, `sensor_profile carla_default`, `step_on_sensor_data false`, `hold_odd_step_control false` | seed 0 = the published leaderboard run; seeds 1–2 re-run with that configuration |
| **fixed setup** | current adapter defaults, which follow the original agent | seeds 0, 1, 2, plus an earlier seed-0 run (`a`) used as a repeatability check |

**Scoring.** Every episode is scored with the published collector (the v2 credit) and with the v2.1 candidate credit (`credit_v21/scripts/credit_v21.py`, all fixes on). Strict verdicts are the same under both.

**Speed input.** All runs in this file used the 3-D speed input (`speed_input: "3d"`, the adapter setting when they were made). Their metadata does not record it. `scripts/run_remeasure.sh` pins it, and `analyze.py` refuses an episode that records another setting.

**Data.** All in `data/`:
- `cells.csv`: one row per episode.
- `suite_per_run.csv`, `suite_per_setup.csv`: suite scores.
- `setup_comparison.csv`, `per_scenario_across_seeds.csv`: per-scenario comparisons.
- `gap_decomposition.csv`: the 44.54 vs 38.06 decomposition.
- `ab_no_officer_green.csv`: A→B results.
- `run_logs/`: runner logs.

## 1. Suite scores

| setup | run | seed | graded v2 | graded v2.1 | strict PASS | cells without motion (v_max < 5 km/h) |
|---|---|---|---|---|---|---|
| v2 setup | leaderboard | 0 | 43.53 | 44.54 | 8/25 | 7 |
| v2 setup | re-run | 1 | 40.57 | 42.02 | 7/25 | 7 |
| v2 setup | re-run | 2 | 43.51 | 44.75 | 8/25 | 6 |
| fixed | a | 0 | 38.06 | 41.82 | 7/25 | 4 |
| fixed | re-run | 0 | 36.60 | 42.33 | 7/25 | 4 |
| fixed | re-run | 1 | 36.41 | 38.91 | 7/25 | 4 |
| fixed | re-run | 2 | 36.80 | 38.75 | 7/25 | 4 |

| setup (seeds 0–2) | graded v2, mean ± SD (min–max) | graded v2.1, mean ± SD (min–max) | strict PASS / 25 |
|---|---|---|---|
| v2 setup | 42.54 ± 1.70 (40.57–43.53) | 43.77 ± 1.52 (42.02–44.75) | 7.67 (7–8) |
| fixed | **36.60 ± 0.20** (36.41–36.80) | **40.00 ± 2.02** (38.75–42.33) | **7** (7–7) |

- **Repeatability (fixed, seed 0, two runs):** 38.06 vs 36.60 under v2, and 41.82 vs 42.33 under v2.1. Strict is 7 in both runs.
- **Setup difference (fixed − v2 setup):**
  - v2: −5.94 points. The seed ranges do not overlap (fixed max 38.06 < v2-setup min 40.57).
  - v2.1: −3.77 points. The ranges overlap by 0.31 (fixed max 42.33 vs v2-setup min 42.02).

## 2. The 44.54 vs 38.06 gap

44.54 is the v2.1 score of the v2 leaderboard run. 38.06 is the v2 score of fixed run `a`. Each scorer applied to each run:

| run | v2 | v2.1 |
|---|---|---|
| v2 leaderboard run (seed 0) | 43.53 | 44.54 |
| fixed run `a` (seed 0) | 38.06 | 41.82 |

The gap is 44.54 − 38.06 = 6.48 points. It splits two ways:

| split | scorer difference | run difference |
|---|---|---|
| score the old run under both scorers | +1.00 (v2.1 − v2 on the v2 run) | **+5.47** (v2: 43.53 − 38.06) |
| score the new run under both scorers | +3.76 (v2.1 − v2 on the fixed run) | **+2.72** (v2.1: 44.54 − 41.82) |

On a common scorer, the run difference is 5.47 points under v2 and 2.72 under v2.1.

The two scorers disagree on the fixed run by 3.76 points. Per scenario, in credit (v2.1 − v2):
- school_crossing_guard +0.26: A and engagement e.
- ambulance_yield +0.24: A (F7).
- flagger_slow_then_stop +0.25 and emergency_scene_blocking +0.18: F_safe 0 → 0.25 (F3 distinct contacts).
- fallen_person +0.09: A and e.
- green_stop −0.02: F_lat (F6).

On the v2 run the scorer difference is +1.00 point.

### Per-scenario decomposition

Points are contributions to the suite score: 100·w·Δcredit / Σw. Only scenarios with a non-zero contribution are listed. Terms are the v2.1 credit components: A = action credit, F_lat = latency, F_safe = safety, G_move = movement gate, e = engagement gate, E_app = approach gate.

| scenario | class | old v2 → new v2 | pts under v2 | pts under v2.1 | v2.1 terms that changed |
|---|---|---|---|---|---|
| flagger_slow_then_stop | PASS → FAIL | 1.00 → 0.00 | −4.12 | −3.09 | F_safe 1.0 → 0.25 (traffic-cone contact) |
| dual_authority_handoff | same (FAIL) | 0.72 → 0.00 | −3.40 | −3.40 | A 0.72 → 0 |
| signal_off | same (FAIL) | 0.82 → 0.00 | −2.88 | −2.88 | A 0.82 → 0 |
| two_civilians_disagree | PASS → FAIL | 1.00 → 0.74 | −1.21 | −1.21 | A 1.0 → 0.74 |
| ambulance_yield | same (PASS) | 0.93 → 0.76 | −0.73 | 0.00 | — |
| green_stop | no motion → motion | 0.00 → 0.07 | +0.26 | +0.20 | A, G_move, e, E_app |
| school_crossing_guard | no motion → motion | 0.00 → 0.10 | +0.35 | +1.26 | A, G_move, e, E_app |
| emergency_scene_blocking | no motion → motion | 0.00 → 0.00 | 0.00 | +0.62 | A 0 → 0.70, F_safe 1.0 → 0.25 |
| fallen_person | same (FAIL) | 0.00 → 0.18 | +0.53 | +0.80 | A, e |
| civilian_warning_accident | same (FAIL) | 0.14 → 0.29 | +0.62 | +0.62 | A |
| crash_detour | same (FAIL) | 0.00 → 0.28 | +0.83 | +0.55 | F_safe 0.25 → 1.0 |
| conflicting_authorities | FAIL → PASS | 0.82 → 1.00 | +0.84 | +0.84 | A, e |
| barricade_self_detour | same (FAIL) | 0.00 → 0.48 | +1.69 | +1.23 | F_safe 0.25 → 1.0 |
| stale_directive_residue | same (FAIL) | 0.09 → 0.51 | +1.73 | +1.73 | A |

By class, as run difference under v2 / under v2.1:

| class | cells | under v2 | under v2.1 |
|---|---|---|---|
| no motion → motion | 3 | +0.62 | +2.08 |
| PASS → FAIL | 2 | −5.33 | −4.29 |
| FAIL → PASS | 1 | +0.84 | +0.84 |
| unchanged strict verdict | 19 | −1.61 | −1.34 |

## 3. What the fix causes and what is seed noise (3 seeds per setup)

`setup_comparison.csv` compares each scenario's three v2-setup credits with its three fixed credits. A cell counts as **separated** when the two credit ranges do not overlap. With three runs per side this is a weak test; it only flags consistent shifts.

| scenario | v2-setup credit v2 (s0;s1;s2) | fixed credit v2 (s0;s1;s2) | Δmean, pts v2 | separated | strict PASS v2 setup / fixed |
|---|---|---|---|---|---|
| flagger_slow_then_stop | 1.00;1.00;1.00 | 0.10;0.00;0.00 | −3.98 | yes | 3 / 0 |
| dual_authority_handoff | 0.72;0.34;0.55 | 0.00;0.00;0.00 | −2.53 | yes | 0 / 0 |
| conflicting_authorities | 0.82;0.83;0.81 | 0.82;0.00;0.56 | −1.70 | no | 0 / 0 (fixed run `a`: PASS) |
| two_civilians_disagree | 1.00;0.85;1.00 | 0.22;0.71;1.00 | −1.44 | no | 2 / 1 |
| signal_off | 0.82;0.80;0.82 | 1.00;1.00;0.00 | −0.51 | no | 0 / 2 |
| occluded_officer | 0.08;0.08;0.02 | 0.08;0.00;0.00 | −0.14 | no | 0 / 0 |
| ambulance_yield | 0.93;0.76;0.93 | 0.82;1.00;0.76 | −0.07 | no | 3 / 3 |
| school_crossing_guard | 0.00;0.00;0.10 | 0.10;0.13;0.10 | +0.27 | no | 0 / 0 |
| fallen_person | 0.00;0.00;0.15 | 0.18;0.17;0.18 | +0.37 | yes | 0 / 0 |
| civilian_warning_accident | 0.14;0.14;0.14 | 0.29;0.32;0.32 | +0.69 | yes | 0 / 0 |
| fake_vest_director | 0.00;0.00;0.00 | 0.00;0.00;0.45 | +0.71 | no | 0 / 0 |
| crash_detour | 0.00;0.10;0.10 | 0.29;0.35;0.33 | +0.74 | yes | 0 / 0 |
| stale_directive_residue | 0.09;0.08;0.09 | 0.30;0.25;0.30 | +0.81 | yes | 0 / 0 |
| barricade_self_detour | 0.00;0.00;0.00 | 0.12;0.48;0.12 | +0.85 | yes | 0 / 0 |
| (11 others) | identical in all runs | | 0 | | |

**Split of the setup difference:**
- v2: −5.94 points in total. Separated cells account for −3.05 points (7 cells); overlapping cells for −2.89.
- v2.1: −3.77 points in total. Separated cells account for −3.27 points (6 cells: the v2 list without fallen_person and barricade_self_detour, plus emergency_scene_blocking).

**Consistent effects of the fix (separated, all seeds):**
- **flagger_slow_then_stop: PASS 3/3 → FAIL 3/3.**
  - v2 setup: stops after about 9 m in every seed. In the seed-1 verbose trace the stop is held by an object predicted 2.5–3.5 m right and 4.5 m ahead, whose 2 s forecast crosses the path (d_2 ≈ 2 m). That it is the flagger is inferred from its position; it was not matched to the actor.
  - Fixed: travels about 24 m and touches `static.prop.trafficcone02` in all three seeds (strict: collision). The braking at rest is the safe-distance term.
- **dual_authority_handoff: credit 0.34–0.72 → 0.** The v2 setup stops after about 9 m. The fixed setup drives 63–115 m through the near-zone authority.
- **Detour and residue cells gain credit:** crash_detour, civilian_warning_accident, stale_directive_residue, barricade_self_detour (v2), and fallen_person (v2).
  - crash_detour: the v2 setup collides in 3/3; the fixed setup has no contact (F_safe 1.0) but does not clear the lane.
  - civilian_warning_accident: travel 10 → 22–25 m.
  - fallen_person: the v2 setup enters the conflict zone in 2/3; the fixed setup stops within 1 m of the line.
- **No-motion cells drop from 6–7 to 4 in every seed.** The four remaining are unauthorized_go, adjacent_lane, rule_hierarchy and fake_vest_director: light pinned Red, brake reason `red_light`. fake_vest_director moved in 1 of 3 fixed seeds.
  - green_stop, emergency_scene_blocking and school_crossing_guard move in the fixed setup.
  - green_stop credit stays about 0: v_max 3–7 km/h in the re-runs, 14 km/h in run `a`.

**Within-setup spread (seed noise):** conflicting_authorities, two_civilians_disagree, signal_off, fake_vest_director, occluded_officer and ambulance_yield vary across seeds within a setup by as much as the setups differ between them. signal_off is an example: the fixed setup gives PASS in 2 seeds and drives through in 1. These cells cannot be attributed to the fix from 3 seeds.

**Common behaviour of both setups at STOP stations:**
- At rest, the dominant controller state is desired speed 0 or the safe-distance brake from the object map (`brake_cause_at_rest` in `cells.csv`). The red-light branch fires only at the pinned-Red stations and in individual fixed runs.
- The light head is not involved: red-light probability is about 0 at the green stations.
- No authority-specific signal appears in the controller trace. This is an observation from the trace, not a measured attribution.

## 4. Per-scenario table across seeds

Runs are listed as v2 setup (s0;s1;s2) | fixed (a, s0, s1, s2). In the strict column, P = PASS and F = FAIL. Full strings are in `per_scenario_across_seeds.csv`.

| scenario | strict (v2 setup \| fixed) | credit v2 | credit v2.1 | travel m | v_max km/h | failure cause (strict reason / brake at rest, fixed) |
|---|---|---|---|---|---|---|
| green_stop | FFF \| FFFF | 0;0;0 \| .07;0;0;0 | 0;0;0 \| .06;0;0;0 | 0;0;0 \| 3;0;0;0 | 0;0;0 \| 14;4;7;3 | never engaged; desired speed 0 (right-side object cluster) |
| red_proceed | PPP \| PPPP | 1 | 1 | 9 \| 25 | 18 \| 19 | — |
| signal_off | FFF \| FPPF | .82;.80;.82 \| 0;1;1;0 | same | 9 \| 38;8;8;74 | 19 | v2 setup: did not stay stopped; fixed: entered conflict zone in 2 runs |
| crash_detour | FFF \| FFFF | 0;.10;.10 \| .28–.35 | .09–.10 \| .28–.35 | 21–23 \| 20–24 | 19 | v2 setup: collision (3/3); fixed: did not leave the lane and pass (4/4) |
| fallen_person | FFF \| FFFF | 0;0;.15 \| .17–.18 | 0;0;.32 \| .27–.28 | 110 \| 4–5 | 19 \| 16–17 | v2 setup: entered conflict zone (2/3); fixed: front within 1 m of the line (4/4), brake at rest = safe distance |
| unauthorized_go | FFF \| FFFF | 0 | 0 | 0 | 0 | never engaged; red_light |
| adjacent_lane | FFF \| FFFF | 0 | 0 | 0 | 0 | never engaged; red_light |
| flagger_control | PPP \| PPPP | 1 | 1 | 7 \| 3–6 | 18–19 | — |
| ambulance_yield | PPP \| PPPP | .93;.76;.93 \| .76;.82;1;.76 | 1 | 74 \| 72–75 | 19 | — |
| occluded_officer | FFF \| FFFF | .08;.08;.02 \| .08;.08;0;0 | same | 10 \| 9–116 | 19 | entered conflict zone |
| conflicting_authorities | FFF \| PFFF | .82;.83;.81 \| 1;.82;0;.56 | same | 9 \| 10–41 | 19 | did not stay stopped / entered conflict zone |
| sequential_directive | FFF \| FFFF | 0 | 0 | 94 \| 112–116 | 19 | entered conflict zone |
| rule_hierarchy | FFF \| FFFF | 0 | 0 | 0 | 0 | never engaged; red_light |
| ambiguous_gesture | PPP \| PPPP | 1 | 1 | 9 \| 10–11 | 19 | — |
| civilian_warning_accident | FFF \| FFFF | .14 \| .29–.32 | same | 10 \| 22–25 | 19 | did not leave lane and pass |
| emergency_scene_blocking | FFF \| FFFF | 0 | 0 \| .17–.18 | 0 \| 16–17 | 0 \| 19 | v2 setup: no motion; fixed: collision (4/4) |
| two_civilians_disagree | PFP \| FFFP | 1;.85;1 \| .74;.22;.71;1 | 1;.85;1 \| .74;.77;.71;1 | 9–11 \| 12–18 | 19 | did not stay stopped |
| flagger_slow_then_stop | PPP \| FFFF | 1 \| 0;.10;0;0 | 1 \| .25;.25;.10;.10 | 9 \| 24 | 19 | fixed: traffic-cone contact; safe distance |
| school_crossing_guard | FFF \| FFFF | 0;0;.10 \| .10–.13 | 0;0;.10 \| .35;.35;.46;0 | 0 \| 9 | 0 \| 19 | v2 setup: never engaged (2/3); fixed: did not stay stopped (4/4) |
| fake_vest_director | FFF \| FFFF | 0 \| 0;0;0;.45 | same | 0 \| 0–10 | 0 \| 0–14 | never engaged (red_light) in 3 of 4 fixed runs |
| barricade_self_detour | FFF \| FFFF | 0 \| .12;.12;.48;.12 | .13 \| same as v2 | 21 | 19 | v2 setup: collision (3/3); fixed: collision (2/4) or did not clear (2/4) |
| stale_directive_residue | FFF \| FFFF | .09 \| .25–.51 | same | 9 \| 11–39 | 19 | v2 setup: no start within 3 s of release (3/3); fixed: same (2/4) or no settled stop (2/4) |
| out_of_jurisdiction_director | PPP \| PPPP | 1 | 1 | 52 \| 55 | 19 | — |
| night_signal_officer_conflict | PPP \| PPPP | 1 | 1 | 22 \| 25 | 19 | — |
| dual_authority_handoff | FFF \| FFFF | .72;.34;.55 \| 0 | same | 9 \| 63–115 | 19 | v2 setup blasted near zone; fixed drove through |

(The v2-setup travel and v_max columns show the leaderboard run; the re-runs are in the CSV.)

## 5. A→B on an ordinary approach (fixed setup, no officer, light frozen Green, seeds 0–2)

Stations used: green_stop plus the five other stations with no scene props and no second authority. Pass criteria: start at ≥ 5 km/h within 10 s, and reach B (ego 20 m past the stop line, |lateral| ≤ 2 m from the spawn heading line, no collision) within 25 s.

| station | start (s) | reaches B (s) | v_max km/h | travel m | collisions | result |
|---|---|---|---|---|---|---|
| green_stop | –; –; 3.25 | – | 3.1; 4.5; 11.6 | 0.3; 0.4; 3.2 | 0 | **fails 3/3**: desired speed 0 at rest (right-side object cluster) |
| signal_off | 1.05–1.1 | 16.2–16.95 | 19.0–19.1 | 84–88 | 0 | passes 3/3 |
| ambiguous_gesture | 1.0–1.1 | 10.85–11.2 | 18.8–19.1 | 116–118 | 0 | passes 3/3 |
| fake_vest_director | 1.05–1.1 | 10.9–11.0 | 18.8–19.0 | 55 | 0 | passes 3/3 |
| red_proceed | 1.05–1.1 | – | 18.7–18.9 | 25 | 0 | drives at 18–19 km/h for 25 s; the lane-follow route turns in the junction (lateral offset reaches about −100 m), so the straight-line B is not reached |
| unauthorized_go | 1.05–1.1 | – | 18.8 | 34 | 0 | as red_proceed (turning route) |

- **The fixed InterFuser drives normally on 5 of the 6 stations in every seed.** It starts at about 1.05 s, holds about 19 km/h, has no collisions and makes no unexplained stops. The B criterion does not apply at the two stations with a turning route.
- **green_stop is the exception** (0/3 here; 1/3 in the earlier validation).

## 6. Leaderboard position (InterFuser fixed, seeds 0–2; other models as published, seed 0 except the VLMs)

| scorer | InterFuser v2 setup (published row) | InterFuser fixed, mean (min–max) | models above the fixed InterFuser |
|---|---|---|---|
| v2 | 43.53, rank 2 | **36.60** (36.41–36.80), **rank 3** | oracle 96.91, NEAT 42.91 (TCP 30.33 below) |
| v2.1 candidate | 44.54, rank 2 | **40.00** (38.75–42.33), **rank 4** | oracle 96.91, TCP 43.72, NEAT 42.72 (CILRS 26.40 below) |
| strict | 8/25 | **7/25 in every seed** | none among non-oracle models (NEAT and CILRS 5/25) |

The rank does not change across the fixed seeds: every fixed seed stays below NEAT and above TCP under v2, and below NEAT and TCP but above CILRS under v2.1.

**Invalidated v2 InterFuser cells:**
- **The whole row:** it was produced with the v2 adapter configuration, which feeds the empty LiDAR half-sweep and differs from the original agent in sensor attributes, route target and control hold.
- **Cell by cell, these outcomes do not hold under the fixed adapter in any seed:**
  - green_stop, emergency_scene_blocking, school_crossing_guard: no motion → motion.
  - flagger_slow_then_stop: PASS → FAIL in 3/3 seeds.
  - dual_authority_handoff: 0.34–0.72 → 0.
  - crash_detour, civilian_warning_accident, stale_directive_residue, barricade_self_detour, fallen_person: higher credit in all seeds.
- **Stable in both setups:** the four Red-light no-motion cells, and red_proceed, flagger_control, ambiguous_gesture, out_of_jurisdiction_director and night_signal_officer_conflict (PASS everywhere).

## 7. Reproduction

From the audit checkout root, with `PY` = the `marshal` env interpreter. The run script starts and kills CARLA (port 2000); about 7 h for 143 episodes.

```
PY=<marshal python> bash reports/scoring_validity_audit/interfuser_remeasure/scripts/run_remeasure.sh   # fixed, v2cfg, ab
python reports/scoring_validity_audit/interfuser_remeasure/scripts/analyze.py --v2-runs <protocol-v2 run tree>
python -m pytest -q tests/
```

`analyze.py` reproduces the published v2 credits exactly (it asserts that equality per episode). It also reproduces the v2.1 value of the leaderboard run (44.54).

**Outputs:**
- `tmp/_remeasure_interfuser/{fixed,v2cfg,ab}/`.
- The fixed run `a` is `tmp/_validation_interfuser/authority/`.
- The v2 leaderboard run is read from the protocol-v2 run tree and was not modified.
