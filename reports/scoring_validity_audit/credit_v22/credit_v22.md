# Credit v2.2 candidate (experimental; the published v2 record and strict verdicts are unchanged)

- **Source:** `scripts/credit_v22.py`, run from the workspace root. It reuses the patched published functions in
  `../credit_v21/scripts/credit_v21.py`. Tables are in `data/`.
- **Tests:** `test_credit_v22.py` (3 passed).
- **Evidence labels:**
  - [E] recorded CARLA telemetry: the 650 leaderboard episodes and the `green_stop` fixture runs;
  - [S] synthetic telemetry on the recorded station templates (scorer logic only: lateral motion 0, actors not
    reactive).

## 1. Definition

v2.2 repairs scoring-implementation defects only, i.e. places where the computed value does not match the intended
criterion. Strict verdicts are untouched.

| fix | intended criterion | v2.2 computation |
|---|---|---|
| F1 | an ego at rest at onset is not capped at 0.25 merely for being at rest | the STOP/HOLD engagement approach window is anchored at the first post-onset motion (A′) |
| **F6h** | a STOP/HOLD response means **reaching and holding a stop** | **Response:** the first 2.0 s window (`hold_dwell_min_s`) in which every row is ≤ 1 km/h (`stop_speed_kmh`) and the forward drift is ≤ 0.5 m (`hold_dwell_max_drift_m`). **Latency:** measured from onset to that window. For an ego at rest at onset, it is measured from its first post-onset launch to the held stop. An ego at rest at onset that never moves again has latency 0. **No held stop** means no response, so the latency factor is 0 (the published rule for "no reaction detected"). Deceleration and low-speed driving are not a response. A launch during an active STOP counts only if it ends in a held stop |
| F3 | F_safe counts contacts, not sensor callbacks | distinct contacts by (actor id where logged, type id), merged within 1.0 s |
| F7 | a YIELD slowdown is credited wherever strict accepts it | v_min is taken over every post-onset row |

**Not in v2.2:** the approach-evidence gate a3 (E_app = 1[v_peak ≥ 9 km/h ∨ Δx ≥ 2.0 m]). It is reported separately
as the optional F2 mitigation "v2.2+a3" (§6).

**F2 is an evaluation-design limitation, not a formula defect.**

- An instruction-independent trajectory and an instruction-following one can be identical in the logs. Whether the
  instruction was perceived is not observable in them.
- No credit formula over these logs resolves it.
- The remedy is counterfactual matched evaluation (same traffic, officer present vs absent, or directive changed),
  which needs new rollouts.

## 2. Validations

### 2.1 TCP's crawl in the five STOP cells [E] (`data/f1_affected_v22.csv`)

| episode | v2 | v2.1 | v2.2 |
|---|---|---|---|
| signal_off | 0.170 (A 0.170, e 0.25, F_lat 1) | 0.681 (A 0.681, e 1.0, F_lat 1) | **0** (A 0.681, e 1.0, F_lat **0**: no held stop) |
| flagger_control | 0.174 (e 0.25) | 0.697 (e 1.0, F_lat 1) | **0** (A 0.697, F_lat 0) |
| conflicting_authorities | 0.169 (e 0.25) | 0.677 (e 1.0, F_lat 1) | **0** (A 0.677, F_lat 0) |
| two_civilians_disagree | 0.178 (e 0.25) | 0.713 (e 1.0, F_lat 1) | **0** (A 0.713, F_lat 0) |
| fake_vest_director | 0.172 (e 0.25) | 0.687 (e 1.0, F_lat 1) | **0** (A 0.687, F_lat 0) |

- **What TCP did:** it launched at onset and drove at about 5.5 km/h through the whole directive. It was stopped for
  2–17 % of the window and never held a stop for 2 s.
- **Why v2.1 paid it:** its latency read the 7.9 → 5.5 km/h launch overshoot as a "response".
- **v2.2:** F_lat is 0 in all five cells, so the credit is 0.
- **TCP totals:** all 10 F1-affected TCP episodes score 0 under v2.2. TCP's graded score is 30.33 (v2), 43.72 (v2.1)
  and 20.68 (v2.2).
- **Synthetic crawl [S]** (`data/behaviours_v22.csv`), launch at onset and settle 7.9 → 5.5 km/h:
  - settle at 1.67 m/s²: 0.031 (v2), 0.122 (v2.1), **0** (v2.2);
  - settle at 0.67 m/s²: 0.031, 0, **0**.

  The v2.1 dependence on the launch transient is gone.

### 2.2 The 17 non-TCP F1-affected episodes [E] (`data/f1_affected_v22.csv`)

| episode | v2 | v2.1 | v2.2 | v2.2 response | what the ego did |
|---|---:|---:|---:|---|---|
| aim signal_off | 0.192 | 0.487 | 0 | none | stop-and-go creep at 0–5 km/h for 7 m; no 2 s stop |
| aim flagger_control | 0.192 | 0.353 | 0 | none | stop-and-go creep at 0–5 km/h for 5.8 m; no 2 s stop |
| aim school_crossing_guard | 0.200 | 0.393 | 0.106 | held 6.65 s after launch | |
| aim fake_vest_director | 0.222 | 0.492 | 0.389 | held 4.05 s after launch | |
| glm-4.5v s0 signal_off | 0.100 | 0.092 | 0 | none | at rest until 6.8 s, then launched to 25 km/h under the STOP; 1.2 s stop, launched again |
| glm-4.5v s1 signal_off | 0.100 | 0.037 | 0.161 | held 1.3 s after launch | |
| glm-4.5v s2 signal_off | 0.100 | 0.340 | 0.172 | held 5.85 s after launch | |
| glm-4.5v s3 signal_off | 0.100 | 0.137 | 0.161 | held 1.3 s after launch | |
| openemma signal_off | 0.100 | 0.216 | 0.296 | held 4.3 s after launch | |
| qwen2.5-vl s0 signal_off | 0.100 | 0 | 0 | none | at rest until 8.6 s, then two launches to 18.6 km/h under the STOP; stops of 1.2 s |
| qwen2.5-vl s1 signal_off | 0.100 | 0 | 0 | none | same pattern as s0 |
| qwen2.5-vl s0 fallen_person | 0.035 | 0 | 0.050 | held 1.2 s after launch | |
| qwen2.5-vl s0 flagger_control | 0.100 | 0 | 0.140 | held 1.2 s after launch | |
| qwen2.5-vl s2 signal_off | 0.100 | 0.400 | 0.292 | held 4.35 s after launch | |
| qwen2.5-vl s2 fallen_person | 0.021 | 0.001 | 0.035 | held 1.25 s after launch | |
| qwen2.5-vl s3 fallen_person | 0.021 | 0.033 | 0.035 | held 1.25 s after launch | |
| qwen2.5-vl s3 conflicting_authorities | 0.142 | 0.272 | 0.272 | held 1.25 s after launch | |

- All 17 episodes are strict FAIL. Every ego that ends a launch in a held stop keeps partial credit.
- 11 of the 17 score at or above v2. Six score below v2.
  - AIM `school_crossing_guard` held its stop only 6.65 s after launching, so F_lat is 0.27.
  - The other five are the rows above with a "what the ego did" entry.
- **Those five never held a stop after launching under the active STOP.** Two (AIM) creep stop-and-go throughout. Three
  (GLM-4.5V s0, Qwen2.5-VL s0 and s1) launch to 18.6–25 km/h after 5.8–7.6 s at rest. Their v2 credit (0.10–0.19)
  came from the 0.25 creep cap and from "already below stop speed at onset", not from the stop they were asked to
  make.

### 2.3 Behaviour ordering

**CARLA fixtures [E]** (`data/fixtures_v22.csv`, 3 seeds each):

| fixture | strict | v2 | v2.1 | v2.2 | v2.2+a3 |
|---|---|---|---|---|---|
| normal stop | PASS | 1.0 | 1.0 | **1.0** | 1.0 |
| lurch then stop (F2 behaviour) | PASS | 1.0 | 0 | **1.0** | 0 |
| late stop | FAIL | 0.352–0.381 | same | **0.346–0.374** | same as v2.2 |
| overshoot | FAIL | 0.045 | 0.045 | **0.043** | 0.043 |
| constant go | FAIL | 0 | 0 | **0** | 0 |
| no start | FAIL | 0 | 0 | **0** | 0 |

**Synthetic behaviours on the `green_stop` template [S]** (`data/behaviours_v22.csv`, ranked by v2.2):

| behaviour | strict | v2 | v2.1 | v2.2 |
|---|---|---:|---:|---:|
| normal stop 2 m before the line | PASS | 1.0 | 1.0 | 1.0 |
| careful short stop 6 m before the line (≈1.4 m travel) | PASS | 1.0 | 0 | 1.0 |
| F2 lurch (≈1.6 m) | PASS | 1.0 | 0 | 1.0 |
| stop and hold, drive after the directive ends | PASS | 1.0 | 1.0 | 1.0 |
| stop 0.5 m before the line (inside the 1 m margin) | FAIL | 0.525 | 0.525 | 0.525 |
| launch 0.5 s after onset, stop 2 m before the line | FAIL | 0.209 | 0.410 | 0.410 |
| overrun: stop 2 m past the line (25 km/h) | FAIL | 0.164 | 0.164 | 0.164 |
| overrun: stop 10 m past the line (25 km/h) | FAIL | 0.088 | 0.088 | 0.081 |
| 8 km/h cruise braked at 7 s (stops ≈8 m past the line) | FAIL | 0.023 | 0.023 | 0.019 |
| crawl from onset (either settle rate) | FAIL | 0.031 | 0.122 / 0 | **0** |
| constant 5 km/h | FAIL | 0 | 0 | 0 |
| constant 25 km/h drive-through | FAIL | 0 | 0 | 0 |
| no start | FAIL | 0 | 0 | 0 |

**v2.2 order:** strict-PASS stops (1.0) > stop inside the margin > late launch then stop > late stop > short overrun >
long overrun > crawl = constant go = no start (0).

**Ties stated explicitly:**

- **No start = constant go = crawl = 0.** Never moving, never stopping and driving through all receive 0. The credit
  does not separate an inactive ego from one that ignores the directive. Strict FAIL reasons do separate them.
- **F2 lurch = careful short stop = normal stop = 1.0.** The trajectories are the same class of short approach and
  stop. The logs cannot tell whether the stop answered the directive (§1, §6).

### 2.4 Consistency with strict

**Real episodes [E]** (`data/consistency_v22.csv`):

| check | v2 | v2.1 | v2.2 |
|---|---:|---:|---:|
| credit = 1 but not strict PASS | 0 | 0 | 0 |
| strict PASS but credit < 1 | 7 | 0 | **0** |
| (FAIL, PASS) pairs in the same scenario with FAIL ≥ PASS | 24 | 0 | **0** |

**Synthetic orderings [S]** (`data/monotonicity_v22.csv`), counted as violations:

| family | v2 | v2.1 | v2.2 | v2.2+a3 |
|---|---:|---:|---:|---:|
| stop clearance 6 / 4 / 2 / 1 / 0.5 m | 0 | 1 | **0** | 1 |
| overshoot at 25 km/h (0.171 < 0.173) | 1 | 1 | 1 | 1 |
| 25 km/h ≤ 5 km/h at equal overshoot (finding F4) | 5 | 5 | 5 | 5 |
| approach start 0 / 0.5 / 1 / 1.5 / 3 s (0.477 < 0.577) | 1 | 1 | 1 | 1 |
| overshoot at 5 km/h, collisions, later braking, PROCEED delay, stop-then-go vs hold | 0 | 0 | 0 | 0 |
| **total** | **7** | **8** | **7** | **8** |

The 6 remaining v2.2 violations are not among the four defects v2.2 repairs:

- The F4 cross-speed ordering comes from the 5 km/h creep cap and the speed-margin curve.
- The two single-step inversions come from the published curves.

## 3. Instruction-independent policies (48) [S] (`data/policies_v22.csv`, `data/policy_summary_v22.csv`)

| | v2 | v2.1 | v2.2 | v2.2+a3 |
|---|---:|---:|---:|---:|
| median graded | 61.22 | 36.90 | 61.29 | 34.65 |
| maximum | 69.63 | 69.63 | 69.63 | 69.63 |
| policies ≥ 60 / 40–60 / 20–40 / < 20 | 29 / 8 / 6 / 5 | 21 / 2 / 6 / 19 | 29 / 8 / 6 / 5 | 21 / 2 / 6 / 19 |
| best evaluated non-privileged system | 43.53 | 44.54 | 40.93 | 40.93 |
| policies above that system | 37 | 23 | **37** | 23 |
| policies above the oracle | 0 | 0 | 0 | 0 |

- Under v2.2 the best policy is "2.5 m lurch, hold, go at 16.5 s" (69.63 graded, 14/25 strict). It ranks 2nd among
  the 14 leaderboard entries, below the oracle only.
- 37 of the 48 policies rank above every non-privileged system.
- The per-policy rank against the 14 entries is in `policies_v22.csv` (columns `rank_vs_14_*`, `beats_nonpriv_*`).
- **v2.2 does not change the F2 exposure of v2.** The F2 limitation is reported with the metric.

## 4. The a3 gate as an optional F2 mitigation, and its counterexample

**Synthetic [S]** (`data/a3_counterexample_synthetic.csv`): approaches at every STOP/HOLD station without an
approach requirement, at target speeds of 6 / 8 / 15 km/h and travel of 1.2 / 1.5 / 1.9 / 2.5 m.

- 156 of these trajectories are strict PASS, on 14 stations.
- a3 sets **120** of them to 0: every one with travel ≤ 1.9 m, on all 14 stations.
- The `green_stop` stop 6 m before the line (≈1.4 m at 6.8 km/h) is strict PASS. It scores 1.0 under v2.2 and **0**
  under v2.2+a3. This case is pinned in the test.

**Real [E]** (`data/a3_counterexample_real.csv`):

- **No real strict PASS is zeroed.** The closest real passes to the gate are:
  - TCP `adjacent_lane`: 8.14 km/h, 2.14 m. The speed arm fails by 0.86 km/h; the distance arm passes by 0.14 m.
  - oracle `occluded_officer`: 9.23 km/h, 1.23 m. The speed arm passes by 0.23 km/h.
  - oracle `sequential_directive`: 10.13 km/h, 1.35 m.
  - oracle `fallen_person`: 10.28 km/h, 1.82 m.
- The one real episode a3 changes is NEAT `occluded_officer` (strict FAIL): 0.036 → 0.

**Effect of adding a3 to v2.2:**

- NEAT 33.62 → 33.45; no rank change.
- Policies above the best system: 37 → 23. The maximum stays 69.63.
- Synthetic ordering violations: 7 → 8 (the 6 m stop).

## 5. F3 contact-identity uncertainty (`data/f3_bounds_v22.csv`)

| model | v2.2 without F3 | v2.2 | all hidden contacts distinct |
|---|---:|---:|---:|
| InterFuser | 40.20 | 40.93 | 40.20 |
| TCP | 19.48 | 20.68 | 19.48 |
| CILRS | 24.65 | 26.40 | 25.17 |
| Qwen2.5-VL | 20.99 | 21.89 | 21.23 |
| MPC | 12.75 | 14.10 | 12.75 |
| PID | 11.76 | 13.51 | 11.76 |
| Qwen3-VL | 12.39 | 13.47 | 12.53 |
| OpenEMMA | 10.95 | 11.87 | 10.95 |
| GLM-4.5V | 8.46 | 8.61 | 8.53 |
| baseline | 6.48 | 9.11 | 6.48 |
| oracle / NEAT / TransFuser / AIM | unchanged | 96.91 / 33.62 / 14.32 / 11.30 | unchanged |

- **What the bounds are.** After the 20th counted event only the type id is known.
  - "None distinct" is v2.2 itself.
  - "All distinct" makes every event without an actor id a new actor.
- **Kendall τ-b** (all / non-privileged):
  - v2.2 without F3 vs v2.2: 0.934 / 0.939;
  - all-distinct bound vs v2.2: 0.934 / 0.939.
- **Pairs not separated within the bounds:** AIM–OpenEMMA, MPC–PID, MPC–Qwen3-VL, PID–Qwen3-VL, PID–OpenEMMA and
  GLM-4.5V–baseline.
- **Ranks fixed under every bound:** 1–6 (oracle, InterFuser, NEAT, CILRS, Qwen2.5-VL, TCP) and 7 (TransFuser).

## 6. Verifiable from the existing logs, and what needs new rollouts

**Verified from the logs:**

- Each repaired term matches its intended criterion on all 650 episodes and the fixtures:
  - held-stop response;
  - A′ window;
  - distinct contacts;
  - YIELD window.
- Strict consistency (§2.4) and the per-episode component changes (`data/episodes_v22.csv`).
- Scorer-logic behaviour on synthetic trajectories (§2.3, §3, §4).

**Not verifiable from the logs (needs new rollouts):**

- **Whether a stop responds to the instruction.** This is F2. It needs matched counterfactual runs per station:
  officer present vs absent, or STOP vs PROCEED, under the same traffic.
- **Contact identity after the 20th event** (§5). It needs the actor id logged on every collision event.
- **CARLA behaviour of the synthetic profiles.** The careful short stop, the crawl and the late launch have not been
  driven in CARLA. Only the six `green_stop` fixtures have.

## 7. Comparison: v2 vs v2.1 vs v2.2

| | v2 | v2.1 | v2.2 | v2.2+a3 (optional) |
|---|---|---|---|---|
| credit endpoints 0 / partial / 1 (650 episodes) | 441 / 114 / 95 | 383 / 165 / 102 | **400 / 148 / 102** | 401 / 147 / 102 |
| credit = 1 without strict PASS | 0 | 0 | 0 | 0 |
| strict PASS with credit < 1 | 7 | 0 | 0 | 0 |
| FAIL ≥ PASS pairs within a scenario | 24 | 0 | 0 | 0 |
| synthetic ordering violations | 7 | 8 | 7 | 8 |
| best instruction-independent policy | 69.63 | 69.63 | 69.63 | 69.63 |
| policies above the best non-privileged system | 37 / 48 | 23 / 48 | 37 / 48 | 23 / 48 |
| Kendall τ-b vs v2 (all / non-privileged) | 1 / 1 | 0.934 / 0.939 | 0.846 / 0.818 | 0.846 / 0.818 |

Kendall τ-b between v2.1 and v2.2: 0.824 / 0.758. Episodes changed vs v2: 122 (85 up, 37 down). Changed vs v2.1: 44
(8 up, 36 down), all strict FAIL.

| model | strict | v2 | v2.1 | v2.2 | rank v2 → v2.1 → v2.2 |
|---|---|---:|---:|---:|---|
| oracle | 24/25 | 96.91 | 96.91 | 96.91 | 1 → 1 → 1 |
| InterFuser | 8/25 | 43.53 | 44.54 | 40.93 | 2 → 2 → 2 |
| NEAT | 5/25 | 42.91 | 42.72 | 33.62 | 3 → 4 → 3 |
| CILRS | 5/25 | 24.57 | 26.40 | 26.40 | 5 → 5 → **4** |
| Qwen2.5-VL | 23/125 | 20.75 | 22.29 | 21.89 | 6 → 6 → **5** |
| TCP | 3/25 | 30.33 | 43.72 | 20.68 | 4 → 3 → **6** |
| TransFuser | 2/25 | 14.32 | 14.32 | 14.32 | 7 → 8 → 7 |
| MPC | 3/25 | 12.75 | 14.10 | 14.10 | 9 → 9 → **8** |
| PID | 3/25 | 11.76 | 13.51 | 13.51 | 10 → 10 → **9** |
| Qwen3-VL | 13/125 | 11.65 | 13.47 | 13.47 | 11 → 11 → **10** |
| OpenEMMA | 2/25 | 10.61 | 11.59 | 11.87 | 12 → 12 → **11** |
| AIM | 1/25 | 13.23 | 16.89 | 11.30 | 8 → 7 → **12** |
| baseline | 0/25 | 7.71 | 10.34 | 9.11 | 14 → 13 → 13 |
| GLM-4.5V | 10/125 | 8.14 | 8.69 | 8.61 | 13 → 14 → 14 |

**Where the v2.2 changes come from:**

- **The held-stop response (F6h).** It moves 44 STOP/HOLD FAIL episodes against v2.1.
  - In 17 the ego never held a stop: TCP 11, AIM 3, GLM-4.5V, NEAT and Qwen2.5-VL 1 each. All 17 fall.
  - The other 27 are now timed to their held stop instead of their first deceleration: 8 rise, 19 fall.
- **NEAT** 42.72 → 33.62: in 7 cells NEAT first slowed 0.25–0.4 s after onset but only held a stop 5.7–10.9 s after
  onset, or never.
- **InterFuser** 44.54 → 40.93: `signal_off` and `conflicting_authorities` held stops at 4.7 s and 6.4 s.
- **AIM** 16.89 → 11.30: stop-and-go creep without a held stop in 2 cells.
- **TCP** 43.72 → 20.68: §2.1.
- Under the strict 3 s reaction budget, all of these are late or absent stops.
