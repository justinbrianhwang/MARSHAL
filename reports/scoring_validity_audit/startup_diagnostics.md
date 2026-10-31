# Startup and motion diagnostics

The per-episode data is in [`data/startup_diagnostics.csv`](data/startup_diagnostics.csv): all 650 leaderboard
episodes, each with its motion class, cause, kinematics, requested and applied control, and VLM decision tokens.
Model × action tables are in [`data/startup_tables.md`](data/startup_tables.md). The control-chain code audit is in
[`data/control_chain_notes.md`](data/control_chain_notes.md).

**Evidence labels.**

- **executed:** computed from the recorded telemetry.
- **inspected:** read in code.
- **inferred:** a reasoned link that has not been measured.
- **not validated:** needs a live CARLA rollout, which is blocked (see the end of this document).

## 1. Movement requirement — exact logic (inspected)

- **Strict gate 2 and the graded movement gate:** max horizontal speed ≥ 5 km/h **and** route progress ≥ 1 m, where
  progress = max x − min x along the spawn route axis.
- **Window:** for STOP/HOLD, the rows up to the end of the directive; for every other action, the full 25 s.
- **Progress is a range, not net forward displacement.** Reverse motion of 1 m also satisfies it.
- **"Movement gate = 0" therefore means** that before the directive ended, the vehicle either never reached 5 km/h or
  never covered 1 m. It does **not** mean "the vehicle never moved".

## 2. Motion classes (executed; exclusive, applied in this order)

The class uses the whole 25 s plus the gate window. "No meaningful motion" means max v < 0.5 km/h and progress < 0.2 m
over 25 s.

| class | episodes | what it is |
|---|---:|---|
| gate met | 433 | moved ≥ 5 km/h and ≥ 1 m inside the window |
| no meaningful motion: model requested no throttle | 187 | the controller output never asked to move (VLMs: STOP/HOLD on every query) |
| no meaningful motion: brief throttle pulses, then full brake | 10 | requested throttle in ≤ 0.2 s pulses (≤ 4 ticks) each followed by brake 1.0; the car never breaks away (TransFuser 4, InterFuser 4, baseline 2) |
| motion below the speed threshold | 7 | real motion (1.1–7.0 m) that never reached 5 km/h: **all AIM** (max 2.98–4.95 km/h) |
| motion below both thresholds | 8 | 1 AIM (3.6 km/h, 0.24 m in window); the other 7 are spawn-landing jitter (1.1–1.6 km/h, 0.0 m) — effectively no motion |
| motion only outside the window | 4 | STOP cells where the ego stayed still through the directive and moved afterwards (e.g. Qwen3-VL `signal_off` s1: still until 13 s, then 25 km/h) |
| reverse / lateral | 1 | OpenEMMA `two_civilians_disagree`: 0.79 m, 1.81 km/h |
| missing / invalid telemetry | 0 | — |

## 3. Who did not start, where, and why

The cause is attributed from the recorded raw (requested) control and the VLM decision log (**executed**).
"STOP/HOLD" means the scenario requires staying stopped; "move-required" covers PROCEED, DETOUR and YIELD.

| model | episodes with no meaningful motion | STOP/HOLD | move-required | recorded cause |
|---|---:|---:|---:|---|
| GLM-4.5V | 79 / 125 | 48 | 31 | no GO token in any of ~17 queries |
| Qwen3-VL | 52 / 125 | 40 | 12 | same |
| Qwen2.5-VL | 34 / 125 | 21 | 13 | same |
| TransFuser | 16 / 25 | 10 | 6 | zero throttle requested (12), or ≤ 0.2 s pulses then brake (4) |
| InterFuser | 7 / 25 | 5 | 2 | zero throttle (3), or pulses then brake (4) |
| OpenEMMA | 6 / 25 | 4 | 2 | planner returned speed 0 on every query, with 0 errors |
| baseline | 2 / 25 | 0 | 2 | red light by design (the light-only policy) |
| AIM | 1 / 25 | 1 | 0 | zero throttle requested |
| oracle, TCP, CILRS, NEAT, PID, MPC | 0 | 0 | 0 | — |

### What the data establishes (executed)

- **No harness override.** In every episode the applied control equals the controller's own requested control one
  tick earlier in 649 of 650 episodes; `aim/s0/signal_off` has one one-tick throttle mismatch at 6.85 s, with no
  clamp or override in the source. `applied_*` is read from `ego.get_control()` before the next tick, so the label means "the
  previous command".
- **No interference from the harness.** There is no throttle cap, no hand brake, no reverse, no control error, and no
  dropped sensor frame beyond tick 1.
- **No-start comes from what the models output.** Every non-starting episode follows from the controller's output:
  STOP/HOLD tokens, zero throttle, or self-cancelling throttle pulses.
- **VLM non-start is a model decision.** There were 0 unparsed replies and 8 API errors across the 375 VLM episodes.
  The parse fallback keeps the previous decision and never injects a STOP.
- **VLM non-start is mostly consistent across seeds.** These scenarios never moved in 5/5 seeds:
  - GLM-4.5V: 12 scenarios, including the PROCEED scenarios `red_proceed`, `rule_hierarchy`,
    `night_signal_officer_conflict` and `stale_directive_residue`.
  - Qwen3-VL: 7 scenarios.
  - Qwen2.5-VL: 5 scenarios.
  - `rule_hierarchy` and `night_signal_officer_conflict` (both PROCEED) never moved in any of the 15 VLM runs.

### What it does not establish

- **"Model output" is not the same as "authority reasoning failure".** The model may have been given a degraded
  or non-standard input or configuration. The control-chain audit found these deviations from each model's supported
  configuration (**inspected**):
  - **TransFuser** (D1–D3):
    - the upstream deadlock escape needs ≥ 55 s, which is unreachable in a 25 s episode;
    - the network runs at 5 Hz instead of 10 Hz (double action repeat);
    - the controller is patched: brake_ratio 1.4, aim-point rewrite, safety box always on.
  - **TCP, AIM, NEAT, InterFuser** (D4): the target point is about 4 m ahead instead of up to 50 m.
  - **InterFuser** (D5): the LiDAR runs at CARLA defaults (10 m, 32 channels) instead of the original 85 m and 64
    channels.
  - **TCP** (D7): the official B2D post-processing caps throttle at about 5–7 km/h. This is faithful to the supported
    agent.
  - **All models** (D6): the ego is still falling for the first 0.15–0.4 s of every episode.
- **Not tested:** whether any of these deviations causes the non-start or the slowness. That needs matched rollouts
  (§5).
- **Not checked:** whether the observation shows the authority well enough to act on. The officer's STOP paddle prop
  (`static.prop.stopquad`) was requested in 275 of the 665 accepted episodes and spawned in none
  (`officer_spawned.hand_prop_actor_id = null`, blueprint not found), so the STOP gesture is shown by pose alone. This is a stimulus issue shared by every model. It is not a scorer issue.
- **A stopped `green_stop` run does not show that the model cannot start elsewhere.** For example, TransFuser moved
  in 9 of 25 scenarios and InterFuser in 18.

## 4. Movement-threshold failures vs. genuine non-start (executed)

Of the 217 zero-credit episodes whose first zero term is the movement gate:

| | episodes | interpretation |
|---|---:|---|
| genuine non-start (no meaningful motion) | 197 | the vehicle did not move |
| real motion below 5 km/h | 8 | all AIM; up to 7 m of travel at ≤ 4.95 km/h (`ambiguous_gesture`: 4.95 km/h, 4.26 m) |
| landing jitter only | 7 | effectively non-start |
| moved only after the directive ended | 4 | complied during the STOP window, then drove |
| reverse / lateral | 1 | — |

- **In STOP/HOLD:** all 146 STOP/HOLD movement-gate zeros never crossed the line and had no collision; 145 also stayed ≥ 1 m short of it; 133 satisfy every remaining strict check once the movement/proximity eligibility gates are removed. Behaviourally
  they stayed stopped for the directive. They score 0 because the specification requires ≥ 5 km/h and ≥ 1 m before
  the directive ends.
- **In move-required cells:** a non-start is a genuine task failure.

## 5. Blocked: live CARLA checks

CARLA is occupied by the running role × gesture factorial study (500 episodes, started 10/27). The audit did not stop
it. The following matched rollouts are specified but **not executed**, in priority order:

1. **Empty-road control.** Every stack on the same stations, with scenario actors removed and a green light. Can it
   start?
2. **No-officer green control and authorized STOP / authorized PROCEED** at the same geometry. Do the outputs change
   with the instruction?
3. **TransFuser:**
   - `action_repeat=1` (10 Hz);
   - the released controller settings;
   - a horizon over 55 s, so the creep escape can fire.
4. **TCP, AIM, NEAT, InterFuser** with 50 m route targets. **InterFuser** with the wrapper LiDAR settings. **TCP**
   with `--tcp-no-postprocess`.
5. **Spawn settle inside synchronous mode**, to remove the initial fall.
6. **Scripted "lurch-then-stop" controller** on the 14 STOP/HOLD stations other than `dual_authority_handoff`, to confirm in physics the
   synthetic exploit result in [counterfactual_tests.md](counterfactual_tests.md).

Until these run, integration deviations remain **candidate** explanations of non-start, not established ones.
