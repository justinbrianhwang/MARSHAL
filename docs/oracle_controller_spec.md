# MARSHAL Oracle Controller — implementation spec (for the delegated agent)

## Goal
Implement the **Track-A Oracle** driving policy: the *expected-behaviour
reference driver*. It is privileged (it receives the full episode ground-truth
E-tuple) and must produce the **correct authority-aware behaviour** in every one
of the 9 MARSHAL scenarios. It is the upper-bound baseline and the reference
trajectory other agents are scored against.

Fill in `marshal_bench/controllers/oracle.py` (class `OracleController`). You may
add helper modules under `marshal_bench/controllers/`. **Do not change the
controller interface or `run_scenario`'s call sites.**

## The contract (already in code)
`marshal_bench/controllers/base.py` → `EpisodeController`:
```
setup(world, ego, ground_truth, carla)        # once, before the loop
step(observation, dt) -> carla.VehicleControl  # every sim tick (20 Hz)
teardown()                                      # once, after
report_target() -> Optional[str]                # for the TAA metric
```
`run_scenario` ticks the world synchronously, calls `step` each tick, and
applies the returned `carla.VehicleControl` to the ego. Return `None` to coast.

## `ground_truth` (privileged E-tuple) — keys available in `setup`
```
M_map, J_junction{x,y,z,yaw}, L_light_state ("Red"/"Green"/"Off"/...),
A_authority{type, valid}, G_gesture ("STOP"/"PROCEED"/"LEFT"/"RIGHT"/"SLOW"/"IDLE"),
T_target_relation ("ego"/"adjacent_lane"/...), S_safety_context{...scene cfg...},
V_visibility, W_weather, Y_expected_action ("STOP"/"PROCEED"/"DETOUR"/"HOLD"/"YIELD"),
ego_spawn{x,y,z,yaw}, stop_line{x,y,z} (may be None), officer_transform{x,y,z,yaw},
target_speed_kmh, max_reaction_time_sec
```

## `observation` — keys available each `step`
```
sim_time, ego_x, ego_y, ego_z, ego_yaw, ego_speed (m/s), ego_speed_kmh,
tl_state, in_junction (bool), ground_truth (same dict as above)
```

## Required behaviour per scenario (Y = expected action)
| scenario | Y | what the oracle must do |
|----------|---|--------------------------|
| green_stop | STOP | decelerate and stop **before** `stop_line` / officer, despite green |
| red_proceed | PROCEED | proceed smoothly through the intersection (authorized override) |
| signal_off | STOP | stop before the officer (signals OFF, valid STOP) |
| crash_detour | DETOUR | follow officer LEFT: change one lane left, pass the wreck, continue |
| fallen_person | STOP | no officer — stop before the prone person on the road |
| unauthorized_go | STOP | command is **unauthorized** → ignore it, obey the red light (stay stopped) |
| adjacent_lane | HOLD | gesture targets the **adjacent** car, not ego → hold at the red |
| flagger_control | STOP | stop at the lane closure / flagger |
| ambulance_yield | YIELD | slow and pull to the side; let the ambulance pass, then resume |

## Recommended approach
- Lane-following: use CARLA's `agents.navigation` — e.g. `BasicAgent` for
  geometric path/lane tracking at `target_speed_kmh`, then **override** the
  longitudinal command for the authority rule. The nav package is not in the
  wheel; call `from marshal_bench.utils.carla_api_compat import
  ensure_agents_on_path; ensure_agents_on_path()` before importing it.
- Longitudinal policy: STOP/HOLD → ramp brake so the ego halts before
  `stop_line` (or the officer / hazard) with a comfortable decel (respect
  `max_reaction_time_sec`); PROCEED → maintain target speed through the junction;
  YIELD → reduce speed, bias steer to the lane edge, resume after the ambulance
  passes (use `S_safety_context` / actor positions from `world`).
- DETOUR → a single lane change to the left using the map waypoint's
  `get_left_lane()`, then continue.
- `report_target()` should return `T_target_relation` (oracle knows the GT).

## Constraints
- CARLA **0.9.16**. Run everything in the conda env **`marshal`**:
  `C:/Users/sunju/miniconda3/envs/marshal/python.exe`.
- Use the `carla` module handed to `setup` (don't re-import a different one).
- Author for synchronous mode at 20 Hz; `step` must be cheap (no blocking).

## How to test (CARLA must be running on Town03, port 2000)
```
C:/Users/sunju/miniconda3/envs/marshal/python.exe scripts/run_marshal_officer_demo.py \
    --scenario green_stop --town Town03 --controller oracle
```
Then inspect `outputs/marshal_runs/<episode_id>/result.json` → `marshal_metrics`.
**Success = the oracle scores AOC=1 / FOA=1 / TAA=1 / SBO=1 / CRI=0 (and a small
RTL) on every applicable scenario.** Iterate scenario by scenario:
green_stop, red_proceed, signal_off, crash_detour, fallen_person,
unauthorized_go, adjacent_lane, flagger_control, ambulance_yield.

Metric definitions: `marshal_bench/criteria/marshal_metrics.py`.
Scenario lifecycle / how the controller is driven: `marshal_bench/scenarios/_common.py`
(`run_scenario`, `_build_ground_truth`, `_build_observation`).
