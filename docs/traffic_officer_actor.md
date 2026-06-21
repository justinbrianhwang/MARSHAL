# TrafficOfficer Actor

## Overview

`TrafficOfficer` is the core actor of the MARSHAL benchmark. It wraps a CARLA
walker (`walker.pedestrian.*`) with three additional responsibilities:

1. Performing one of six traffic-control gestures (STOP, PROCEED, LEFT, RIGHT,
   SLOW, IDLE) either via per-tick skeleton bone control or via a debug-overlay
   fallback.
2. Exposing ground-truth semantic metadata (authority type, authority validity,
   active gesture id, target relation, onset time, duration) that downstream
   evaluation criteria and ground-truth-aware agents can consume.
3. Coordinating with `marshal_bench.utils.traffic_light_utils` so that scenarios can
   place an officer command in conflict with a frozen signal state, which is
   the whole point of the benchmark.

MARSHAL needs this actor because no CARLA built-in primitive models an
authorized human overriding a traffic signal. Without it, the benchmark
question "does the ego defer to lawful authority when it contradicts the
signal?" cannot be posed.

## CARLA version tested

- CARLA: 0.9.16 (Windows build under `CARLA_0.9.16/`).
- Python: 3.12 (CPython, win-amd64). Required wheel:
  `CARLA_0.9.16/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-win_amd64.whl`.
- ScenarioRunner: optional; not bundled in this repo.

The capability layer (`marshal_bench.utils.carla_api_compat.detect_capabilities`) runs
purely off the imported `carla` module, so it is exercised by unit and
import-time tests. Live verification against a running CARLA server (spawning
a walker, calling `set_bones`, freezing a real traffic light) is pending the
user starting `CarlaUE4.exe`.

## API surface used

The actor depends on the following CARLA Python classes and methods. All of
these are routed through `marshal_bench.utils.carla_api_compat`, which falls back
gracefully when a method is missing.

- `carla.Walker`
  - `set_bones(WalkerBoneControlIn)` — apply per-bone relative rotations.
  - `blend_pose(alpha)` — blend manual pose against the running animation.
  - `show_pose()` / `hide_pose()` — toggle manual pose visibility.
  - `get_bones()` — read the live skeleton (used by
    `scripts/dump_walker_bones.py` and by `infer_upper_limb_bones`).
- `carla.WalkerBoneControlIn` — payload for `set_bones`.
- `carla.WalkerAIController` — only used when an officer is configured to walk
  to a transform before the gesture starts; the static officer path does not
  require it.
- `carla.TrafficLight`
  - `set_state(state)`
  - `get_state()`
  - `freeze(True/False)` — pin the entire light group at the junction.
- `carla.DebugHelper` (`world.debug`)
  - `draw_string`, `draw_arrow`, `draw_line`, `draw_point` — used by the
    debug-fallback gesture layer and by `marshal_bench.utils.debug_viz`.

## Capability detection

`marshal_bench.utils.carla_api_compat.detect_capabilities()` introspects the loaded
`carla` module once per process and returns a `Capabilities` dataclass with
these flags:

- `carla_version` — string from `carla.__version__` or inferred from the wheel
  path.
- `has_walker_set_bones`, `has_walker_blend_pose`, `has_walker_show_pose`,
  `has_walker_get_bones` — whether the four skeleton APIs are present and
  callable on `carla.Walker`.
- `has_walker_ai_controller` — whether `carla.WalkerAIController` exists.
- `has_traffic_light_freeze`, `has_traffic_light_set_state` — whether the
  signal-override APIs exist on `carla.TrafficLight`.
- `has_scenario_runner` — whether `srunner.scenariomanager.*` is importable.
- `custom_asset_walker` — set externally by the runner if a non-default
  walker blueprint (e.g. a future police mesh registered via the Unreal
  extension) was used.

Downstream code should branch on these flags rather than catching
`AttributeError`. Example:

```python
caps = detect_capabilities(world)
if caps.has_walker_set_bones and caps.has_walker_blend_pose:
    officer.set_gesture("STOP")           # skeleton path
else:
    officer.use_debug_visuals = True      # fall back to overlay
```

## How to run

```bash
python scripts/run_marshal_officer_demo.py \
  --port 2000 \
  --scenario green_stop \
  --config marshal_bench/configs/demo_green_stop.yaml \
  --debug
```

Other scenarios: `--scenario red_proceed --config marshal_bench/configs/demo_red_proceed.yaml`
and `--scenario signal_off --config marshal_bench/configs/demo_signal_off.yaml`.

The runner connects to a CARLA server on the given host/port, loads the town
declared in the YAML, spawns the ego and officer, freezes the traffic light,
runs synchronous-mode ticks until the expected behavior is observed or
`timeout_sec` is reached, and writes logs to
`outputs/marshal_runs/<episode_id>/`.

## How gesture animation works

Gestures use a two-layer system. The active layer is selected per-tick based on
capability detection and per-actor success of `set_bones`.

1. **Skeleton layer.** `GestureEngine.apply_gesture(actor, state, sim_time)`
   builds a `WalkerBoneControlIn` payload of relative rotations on the upper
   limb bones (and optionally spine/head), calls `actor.set_bones(payload)`,
   then `actor.blend_pose(1.0)` so the manual pose overrides the locomotion
   animation. Bone names follow CARLA's unified skeleton convention
   `crl_*__{C,L,R}` (e.g. `crl_arm__R`, `crl_foreArm__R`, `crl_hand__R`). For
   walker meshes that use a different naming convention,
   `infer_upper_limb_bones(bone_names)` does best-effort substring matching
   (`arm`, `forearm`/`fore_arm`, `hand` plus an `L`/`R`/`left`/`right`
   discriminator) to populate the same logical slots.
2. **Debug-fallback layer.** If skeleton control is unavailable (capability
   missing, bone inference failed, or `set_bones` raised), the officer instead
   draws a colored label `OFFICER: <GESTURE>` floating above its head plus an
   arrow from the officer to the target lane waypoint or the ego vehicle.
   Label color comes from `fallback_label_color` in
   `marshal_bench/configs/officer_gestures.yaml`. This layer is intended for
   debugging and the oracle-command evaluation track, not for sensor-only
   benchmark runs.

Both layers are driven from the shared YAML at
`marshal_bench/configs/officer_gestures.yaml`, so gesture timing/colors/poses can be
tuned without touching code.

## Fallback modes

There are three fallback levels. The runner records which level was active in
`metadata.json` so analyses can be filtered.

- **Level 0 — skeleton OK.** All required upper-limb bones were resolved and
  `set_bones` succeeded. Sensor-only benchmark mode is valid; a vision
  perception module can in principle decode the gesture from the rendered
  walker.
- **Level 1 — partial skeleton.** Some bones (e.g. only the upper arm)
  resolved. The gesture is approximated; the actor still moves. Sensor-only
  benchmark results should be flagged as partial.
- **Level 2 — debug-only.** Skeleton control was unavailable or failed. Only
  the debug overlay is shown. Only the oracle-command track (where the agent
  reads `officer.get_metadata()` directly) is meaningful here; sensor-only
  results must be discarded.

## Known limitations

- CARLA's built-in `vehicle.set_autopilot(True)` Traffic Manager does **not**
  perceive walker gestures. This is intentional for the benchmark — it exposes
  the perception gap that MARSHAL is designed to measure. Custom planners and
  agents must implement officer detection themselves (e.g. via
  segmentation + pose estimation, or via the oracle metadata channel).
- Officer poses are hand-tuned approximations, not motion-captured. They are
  visually distinguishable across the six gesture classes but are not
  photo-real. They are sufficient for benchmark fairness (the same poses are
  used across all evaluated agents) but should not be used as ground truth
  for pose-estimation research.
- On Windows + Python 3.12 the wheel
  `CARLA_0.9.16/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-win_amd64.whl`
  must be installed in your environment. The compat layer will try to
  `sys.path`-insert it as a last resort, but installing it cleanly via
  `pip install <wheel>` is preferred.
- ScenarioRunner is not bundled in this repo. If `srunner` is not importable,
  scenarios run as standalone scripts via
  `scripts/run_marshal_officer_demo.py`. The
  `MarshalGreen/Red/SignalOffScenario` BasicScenario classes are only
  registered when `detect_capabilities().has_scenario_runner` is true.
- `TrafficLight.freeze(True)` affects the entire light group at the junction,
  not just the one light the ego is approaching. For our scenarios this is
  the desired behavior (the whole intersection is held), but it means a
  scenario cannot independently freeze one approach and leave the others
  cycling without using
  `marshal_bench.utils.traffic_light_utils.set_intersection_lights` to drive each
  light individually.

## Using TrafficOfficer in MARSHAL

A minimal custom scenario looks like this:

```python
from marshal_bench.actors.traffic_officer import TrafficOfficer
from marshal_bench.criteria.authority_compliance import AuthorityComplianceCriterion
from marshal_bench.utils.traffic_light_utils import (
    find_relevant_traffic_light,
    set_traffic_light_state,
)

officer = TrafficOfficer(
    world,
    transform=officer_spawn_transform,
    authority_type="police",
    authorized=True,
    use_skeleton=True,
    use_debug_visuals=True,
)
officer.spawn()

tl = find_relevant_traffic_light(world, ego_vehicle)
set_traffic_light_state(tl, "Green", freeze=True)

officer.set_gesture("STOP", onset_time=3.0, duration=8.0, target_relation="ego")

criterion = AuthorityComplianceCriterion(
    ego_vehicle=ego_vehicle,
    officer=officer,
    expected_action="STOP",
    stop_line_location=tl.get_stop_waypoints()[0].transform.location,
    max_reaction_time=3.0,
    metadata=officer.get_metadata(),
)

while not criterion.is_done():
    world.tick()
    officer.tick(world.get_snapshot().timestamp.elapsed_seconds)
    criterion.update()

print(criterion.result())   # PASS / FAIL + latency + min distance to stop line
officer.destroy()
```

## Metadata schema

`TrafficOfficer.get_metadata()` returns a JSON-serializable dict with the
following keys. This dict is also written to
`outputs/marshal_runs/<episode_id>/metadata.json`.

| Key | Type | Description |
|-----|------|-------------|
| `actor_id` | int | CARLA actor id of the spawned walker. |
| `role_name` | str | Walker `role_name` attribute, default `"traffic_officer"`. |
| `blueprint_id` | str | Resolved walker blueprint id (e.g. `walker.pedestrian.0001`). |
| `authority_type` | str | One of `"police"`, `"flagger"`, `"emergency_responder"`, `"unauthorized"`. |
| `authority_valid` | bool | Mirror of the `authorized` constructor argument. |
| `gesture_id` | str | Current gesture: `"IDLE"`, `"STOP"`, `"PROCEED"`, `"LEFT"`, `"RIGHT"`, `"SLOW"`. |
| `target_relation` | str | `"ego"`, `"adjacent_lane"`, `"opposite_direction"`, or `"ambiguous"`. |
| `target_lane_id` | int or null | OpenDRIVE lane id of the target lane, if applicable. |
| `onset_time` | float or null | Sim time (s, episode-relative) when the gesture began. |
| `duration` | float or null | Configured gesture duration (s). |
| `transform` | dict | `{location: [x, y, z], rotation: [pitch, yaw, roll]}` of the officer. |
| `skeleton_control_active` | bool | True iff Level 0 fallback. |
| `debug_visuals_active` | bool | True iff debug overlay is currently being drawn. |
| `custom_asset` | bool | True iff a non-default (custom Unreal) walker blueprint was used. |
| `traffic_light_freeze_active` | bool | True iff the scenario's traffic light was frozen this tick. |
