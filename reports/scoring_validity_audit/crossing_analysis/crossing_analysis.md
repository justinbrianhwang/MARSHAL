# Stop-line crossings: cause analysis (Protocol v2 leaderboard)

Scope: the 650 leaderboard episodes. A crossing is the first 0.05 s telemetry row (t ≤ 25 s) on which the
signed front-bumper distance `stopline_front_m` is negative. 310 episodes cross. Each one is labelled first by
whether the crossing breaks the episode's contract (relevance), then by the mechanism visible in the
**requested** control (`raw_throttle` / `raw_brake`, i.e. what the controller asked for before the actuator)
and, for Track-C, the VLM decision in force on each tick (`vlm_trace.csv: active_action`).

Reproduce from the workspace root:
`python <audit>/crossing_analysis/scripts/crossing_causes.py --runs tmp/_protocol_v2 --audit <audit> --out <audit>/crossing_analysis`
→ `crossing_causes.csv` (one row per crossing episode), `a_max.json`, `crossing_trajectories.png`.

## Definitions and thresholds

| quantity | definition |
|---|---|
| brake request | `raw_brake ≥ 0.3`. Track-C: only while the decision in force is STOP/HOLD/SLOW. Under GO, the brake comes from the base lane-follower's 25 km/h speed regulation (`base_brake`), not from a response to the stop line |
| approach segment | rows after the last row with speed < 0.5 km/h before the crossing (the final launch) |
| required deceleration | `a_req = v² / (2·(d − v·0.05))`, d = `stopline_front_m`, with one tick of actuation lag |
| A_MAX | **4.48 m/s²**: median deceleration the CARLA ego actually reached in this data. Measured over 58 spans of 0.5 s with `applied_brake ≥ 0.9`, entry speed ≥ 15 km/h, no collision. Sensitivity at 6 and 8 m/s² is shown below |
| onset feasibility | Two tests at the onset tick, both with one tick of actuation lag. **Immediate**: `a_req(v_onset, d_onset) > A_MAX` (0 s reaction) means no controller could stop once the directive appeared. **After a 1 s coast**: the same test after travelling 1.0 s at the onset speed (T_REACT). A crossing that passes the immediate test but fails the 1 s test was avoidable only with near-immediate reaction. The class counts are also shown with T_REACT = 0 s |
| phase | crossing before onset / within onset + reaction budget (3.0 s; 3.5 s for occluded_officer and ambiguous_gesture) / after the budget |

**Relevance** (all 310 crossings):

| relevance | n | meaning |
|---|---|---|
| binding | 143 | STOP/HOLD crossing at t ≤ active_end; stale_directive_residue while the STOP is live; rule_hierarchy before t_safe (the pinned scorer's `safe_to_proceed_time`) |
| post_window | 11 | STOP/HOLD crossing after the directive expired (unscored) |
| contract_requires_crossing | 15 | dual_authority_handoff: the hold band (10–17.5 m from spawn) lies past the stop line at 7.41 m, so a compliant run crosses (the oracle crosses and PASSes) |
| expected_crossing | 141 | PROCEED / DETOUR / YIELD crossings. Of these, 52 are **spawned past the line** (red_proceed and night_signal_officer_conflict, front bumper −4.58 m at t = 0: 26 + 26, every model) |

**Mechanism classes** (binding crossings, first match wins). The group says where the cause sits:

| class | rule | group |
|---|---|---|
| spawned_past | `stopline_front_m < 0` on row 0 | scenario geometry |
| rolled_without_throttle | no `raw_throttle ≥ 0.05` before crossing | implementation suspect |
| collision_displaced | collision recorded by the crossing tick and speed < 1 km/h there | collision related |
| crossed_before_onset | crossing before directive onset | geometry / timing |
| infeasible_at_onset_immediate | at the onset tick, with 0 s reaction, `a_req > A_MAX` | scenario geometry |
| tight_onset_margin | stoppable at the onset tick with immediate braking, not stoppable after a 1 s coast at the onset speed | model decision (late reaction); flagged `scenario_tight_margin` as a scenario-design concern |
| no_post_onset_decision | Track-C: no VLM query in [onset, crossing) | geometry / timing |
| stop_decision_not_actuated | Track-C: STOP/HOLD in force while feasible, but no brake requested | implementation suspect |
| creep_across | < 5 km/h at the crossing and throughout the preceding 2 s | model decision |
| never_braked | no brake request in the approach segment | model decision |
| braked_late | at the first approach brake request `a_req > A_MAX` | model decision |
| released_brake | feasible at brake onset, request later dropped below 0.3 | model decision |
| control_not_followed | feasible at brake onset, request held with mean ≥ 0.8, still crossed | implementation suspect |
| braked_insufficiently | feasible at brake onset, request held with mean < 0.8 | model decision |

## Results: binding crossings, model × class

| model | never_braked | braked_late | released_brake | braked_insufficiently | creep_across | tight_onset_margin | collision_displaced | total |
|---|---|---|---|---|---|---|---|---|
| aim |  |  |  |  | 2 |  |  | 2 |
| baseline | 7 |  |  |  |  | 2 | 1 | 10 |
| cilrs | 5 | 1 | 6 |  |  | 1 |  | 13 |
| glm-4.5v | 10 | 3 |  | 1 |  | 5 |  | 19 |
| interfuser | 1 | 1 |  |  |  | 1 |  | 3 |
| mpc | 12 |  |  |  |  | 2 | 1 | 15 |
| neat |  | 1 | 1 |  |  |  |  | 2 |
| openemma | 5 |  |  |  |  | 2 |  | 7 |
| pid | 12 |  |  |  |  | 2 |  | 14 |
| qwen2.5-vl | 14 | 6 |  |  |  | 6 |  | 26 |
| qwen3-vl | 15 | 1 |  |  |  | 10 |  | 26 |
| tcp | 1 | 1 | 2 |  |  |  |  | 4 |
| transfuser | 1 | 1 |  |  |  |  |  | 2 |
| **all** | **83** | **15** | **9** | **1** | **2** | **31** | **2** | **143** |

The oracle has no binding crossing. Track-C models ran 5 seeds (70 STOP/HOLD episodes each, excluding
dual_authority_handoff); all other models ran 1 seed (14). All 143 binding crossings are strict FAIL.

**Groups (binding, n = 143)**

| group | n | classes |
|---|---|---|
| model decision | 141 | never_braked 83, tight_onset_margin 31, braked_late 15, released_brake 9, creep_across 2, braked_insufficiently 1 |
| of which flagged scenario_tight_margin | 31 | tight_onset_margin 31: occluded_officer 21, sequential_directive 10 |
| scenario geometry (infeasible at onset with immediate reaction) | **0** | infeasible_at_onset_immediate is 0: no binding crossing was unstoppable with a full brake requested at the onset tick |
| geometry / timing | 0 | crossed_before_onset and no_post_onset_decision are 0 |
| collision related | 2 | flagger_slow_then_stop: baseline and mpc hit the work-zone block and stop 8 mm and 34 mm past the line at ≈0 km/h. Both are within the scorer's 0.3 m crossing tolerance |
| implementation suspect | **0** | rolled_without_throttle, stop_decision_not_actuated and control_not_followed are all 0 |
| scenario geometry (spawned past) | 0 binding | 52 spawned-past episodes sit in PROCEED scenarios where crossing is the goal |

**Implementation checks behind the 0:**

- Requested vs applied brake: in all 310 approach segments, 0 ticks have an applied brake that differs from the requested brake at the same tick or the previous one.
- Track-C actuation: across all 131,448 leaderboard ticks where STOP or HOLD was the decision in force, 0 have `applied_brake < 0.5` or `applied_throttle > 0.05`.
- Coverage: no vehicle reached the line without a throttle request, and none crossed while holding a feasible full-brake request.

**Sensitivity of the class counts**

| setting | never_braked | braked_late | released_brake | tight_onset_margin | infeasible_at_onset_immediate | other |
|---|---|---|---|---|---|---|
| A_MAX 4.48 m/s² (primary) | 83 | 15 | 9 | 31 | 0 | 5 |
| A_MAX 6 m/s² | 84 | 15 | 9 | 30 | 0 | 5 |
| A_MAX 8 m/s² | 87 | 15 | 9 | 27 | 0 | 5 |
| T_REACT 0 s (no tight-margin test) | 98 | 15 | 10 | 0 (+15 no_post_onset_decision) | 0 | 5 |

The 4 episodes that move at 8 m/s² are all sequential_directive (openemma s0, qwen2.5-vl s3, qwen3-vl s0, qwen3-vl s4).

Onset margins of the 31 tight_onset_margin crossings (immediate a_req vs. a_req after a 1 s coast):

| scenario | n | speed at onset | distance at onset | a_req, immediate | a_req, after 1 s coast |
|---|---|---|---|---|---|
| occluded_officer | 21 | 9.3–11.0 km/h | 1.78–2.13 m | 1.7–2.9 m/s² | line already passed |
| sequential_directive | 10 | 10.3–11.0 km/h | 3.61–3.75 m | 1.1–1.3 m/s² | 5.4–11.3 m/s² |

All 31 were stoppable with a full brake requested at the onset tick (immediate a_req ≤ 2.9 m/s², below A_MAX 4.48).
They are avoidable with near-immediate reaction, so they count as model response. The stop-line distance from
spawn (2.50 m in occluded_officer, 4.33 m in sequential_directive) leaves under 1 s of reaction time at the
launch speed; this is a scenario-design concern recorded in `scenario_tight_margin`. For the 15 Track-C
occluded_officer runs, no VLM query fell between onset (1.0 s) and the crossing (1.45–1.5 s); the next query was
at 1.55 s. The 6 Track-C sequential_directive runs had one query in that interval.

**Timing:** 81 of the 143 binding crossings happen inside onset + reaction budget, 61 after it, and 1 before onset.
53 of the 83 never_braked runs still had `raw_throttle ≥ 0.1` at the line. The other 30 were coasting.

## How each class arises

- **never_braked (83).**
  - Non-VLM (44 of the 83). pid, mpc, baseline and openemma account for 36: they follow the lane and never request brake at the line, crossing at 17–24 km/h. The other 8 are cilrs ×5, interfuser, tcp and transfuser.
  - Track-C (39 of the 83): the decision in force at the crossing is GO in every case.
    - In 20, every decision before the crossing was GO.
    - In 19, the vehicle had first stopped under a STOP/HOLD decision. A later GO decision launched it from standstill across the line. Example: qwen2.5-vl/s1/green_stop with decisions STOP@0.05 › GO@1.55 › STOP@3.10 › HOLD@7.75 › GO@9.25 crosses at 10.5 s.
- **braked_late (15).**
  - Track-C (10 of the 15): a STOP or SLOW decision came into force with the bumper ≤ 1.05 m from the line, after an earlier GO had launched the car. a_req ranged from 17 to > 1000 m/s².
  - interfuser, transfuser and neat on fallen_person, tcp on occluded_officer and cilrs on school_crossing_guard: the first brake request came 1.04 m to −0.06 m from the line.
- **released_brake (9, all non-VLM).** 7 of the 9 are single-tick (0.05 s) brake pulses at 6–25 km/h, with the line still stoppable (a_req 0.2–1.8 m/s²), followed by throttle or coasting. The other 2 (cilrs on fallen_person and unauthorized_go) braked fully for 0.15–0.6 s, then released.
- **braked_insufficiently (1).** glm-4.5v/s0/green_stop decided SLOW (brake 0.3) rather than STOP. It crossed at 11.8 km/h.
- **creep_across (2).** aim crossed at 1.9 and 2.8 km/h, 8.15 s and 12.75 s into fallen_person and sequential_directive.
- **tight_onset_margin (31).** Two scenarios:
  - occluded_officer: the line is 2.50 m from spawn.
  - sequential_directive: the line is 4.33 m from spawn.

  Models that launch on the green before the gesture onset reach 9–11 km/h at t = 1.0 s with 1.8–2.1 m (occluded) or 3.6–3.8 m (sequential) left. A full brake requested at the onset tick stops before the line (a_req 1.1–2.9 m/s²). After a 1 s coast the remaining distance is ≤ 0 (occluded) or ≈ 0.6 m (sequential). 30 of the 31 requested no brake before the line; interfuser on occluded_officer requested brake at 1.15 s (1.67 m out) and released it. Crossings land at 1.45–1.65 s (occluded) and 1.80–1.85 s (sequential). The crossing is a late reaction by the model; the short spawn-to-line distance is a scenario-design concern (`scenario_tight_margin`).

## Representative trajectories (`crossing_trajectories.png`)

1. **pid/s0/green_stop: never_braked (model decision).** Spawn 7.41 m before the line. At onset (1.0 s) the car is at 12.0 km/h with 6.53 m to go; stopping needed 0.87 m/s² (1.83 m/s² after a 1 s reaction). Requested brake stays 0.0 throughout. Requested throttle is 0.65, then about 0.37. The car crosses at 2.30 s at 22.9 km/h and keeps going.
2. **qwen3-vl/s0/green_stop: never_braked (model decision, Track-C).** Decisions GO@0.05 ("no officer/flagger present") and GO@1.55 ("no authority figure present"). At the 1.55 s decision the car was at 18.7 km/h, 4.43 m from the line, and a stop needed 3.2 m/s², which was feasible. The only brake before the line (0.46–0.56 at 2.25–2.30 s) is the base follower capping speed at 25.5 km/h under GO. The car crosses at 2.30 s at 25.6 km/h.
3. **qwen3-vl/s0/occluded_officer: tight_onset_margin (model decision, tight scenario margin).** Spawn 2.50 m before the line. Decision GO@0.05 with requested throttle 0.65. At onset the car is at 10.7 km/h with 1.84 m left; a full brake at that tick needed 2.6 m/s² (feasible), while after a 1 s coast the line is already passed. It crosses at 1.50 s at 17.8 km/h, one tick before the first post-onset query at 1.55 s. That query also returned GO ("no officer/flagger present"), so the perception outcome was GO as well.
4. **cilrs/s0/unauthorized_go: released_brake (model decision).** Spawn 6.16 m before the line. Full brake request at 1.60 s (18.6 km/h, 3.76 m to go, a_req 3.8 m/s², feasible). The brake is released at 1.75 s and throttle 0.75 resumes. A second full brake at 2.20 s comes too late: the car crosses at 2.35 s at 19.7 km/h and stops 0.93 m past the line. Credit is 0.49.
5. **baseline/s0/flagger_slow_then_stop: collision_displaced.** The car approaches at 24.0 km/h with no brake. Speed falls from 24.0 to 0.15 km/h in one tick at the work-zone block, and the car rests 8 mm past the line. The collision sets the crossing depth, not the driving.

## Post-window and contract cases

- **post_window (11, unscored):**
  - conflicting_authorities ×5: qwen2.5-vl s1 and s3, qwen3-vl s2 and s4, tcp
  - signal_off ×2: glm-4.5v s0, qwen3-vl s1
  - ambiguous_gesture ×2: openemma, tcp
  - two_civilians_disagree ×1: tcp
  - school_crossing_guard ×1: tcp

  These cross 0.45–10.95 s after the directive ends. Only two_civilians_disagree has a red light (tcp, 20.3 s), so that one is an unscored red-light entry. The others are green or dark signals.
- **dual_authority_handoff (15):** the contract places the hold band past the stop line, so crossing is required. 14 of the 15 are strict FAIL for band or dwell reasons, not because they crossed.
